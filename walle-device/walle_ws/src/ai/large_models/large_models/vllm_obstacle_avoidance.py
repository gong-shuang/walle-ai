#!/usr/bin/env python3
# encoding: utf-8
# @Author: liang
# @Date: 2025/3/7
import cv2
import json
import time
import rclpy
import threading
from rclpy.node import Node
from std_msgs.msg import String, Bool, Int32
from std_srvs.srv import SetBool, Empty
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from speech import speech
from large_models.config import *
from large_models_msgs.srv import SetString, SetModel
from ros_robot_controller_msgs.msg import BuzzerState
from geometry_msgs.msg import Twist

language = os.environ["ASR_LANGUAGE"]
if language == 'Chinese':
    PROMPT = '''
你作为智能机器人，可以实时识别图像，并在response 中输出相关信息.

我会给你一句话，从我的话中提取距离和动作，将获取的距离(单位是厘米，要转化成毫米)和动作填充进distance_one和action，"action"键下承载一个按执行顺序排列的函数名称字符串数组，当找不到对应动作函数时action输出[]。

##动作函数库
* 往前走：move_forward()
* 左转：turn_left()
* 停止运动：move_backward()
* 右转：turn_right()
* 左平移：move_left()
* 右平移：move_right()
* 加速：move_forward_fast()
* 减速：move_forward_slow()
* 叫一下:buzzer()

格式：
{
    "type": "detect",
    "object": "障碍物",
    "action": ["xx", "xx"],
    "distance_one": [300, 200],
    "response": "xxx"
}

##示例
输入：往前走如果前方30cm有障碍物开始减速，前方20cm有障碍物就左转
输出：{
        "type": "detect",
        "object": "障碍物",
        "action": ["move_forward()", "move_forward_slow()", "turn_right()"],
        "distance_one": [300, 200],
        "response": "已完成避障"
    }
'''
else:
    PROMPT = '''
You are an intelligent robot that can recognize images in real time and output relevant information in the response.
I will give you a sentence, extract the distance and action from my words, and fill the obtained distance (in centimeters, converted to millimeters) and action into distance_one and action. The "action" key carries an array of function name strings arranged in execution order. When there is no corresponding action function, the action outputs [].
## Action function library
* Move forward: move_forward()
* Turn left: turn_left()        
* Stop moving: move_backward()
* Turn right: turn_right()
* Move left: move_left()
* Move right: move_right()
* Speed up: move_forward_fast()
* Slow down: move_forward_slow()
* Make a sound: buzzer()

- Format:
{
    "type": "detect",
    "object": "obstacle",
    "action": ["xx", "xx"],
    "distance_one": [300, 200],
    "response": "xxx"
}

## Example
Input: Move forward. If there is an obstacle 30cm ahead, slow down. If there is an obstacle 20cm ahead, turn left.
Output: {
        "type": "detect",
        "object": "obstacle",
        "action": ["move_forward()", "move_forward_slow()", "turn_right()"],
        "distance_one": [300, 200],
        "response": "Obstacle avoidance completed"
    }   
'''

class VLLMTrack(Node):
    # 空闲轮询周期：20Hz，兼顾 cmd_vel 保活（10Hz 重发）与 CPU 占用
    PROCESS_PERIOD_SEC = 0.05
    # 定时动作（转向/平移等）持续时间与重发周期
    ACTION_DURATION_SEC = 0.6
    ACTION_REPUBLISH_SEC = 0.1

    def __init__(self, name):
        rclpy.init()
        super().__init__(name)
        self.vllm_result = ''
        self.running = True
        self.data = []
        self.start_track = False
        timer_cb_group = ReentrantCallbackGroup()
        self.client = speech.OpenAIAPI(api_key, base_url)
        self.tts_text_pub = self.create_publisher(String, 'tts_node/tts_text', 1)
        self.buzzer_publisher = self.create_publisher(BuzzerState, 'ros_robot_controller/set_buzzer', 10)
        self.mecanum_pub = self.create_publisher(Twist, 'cmd_vel', 1)
        self.create_subscription(Bool, 'tts_node/play_finish', self.play_audio_finish_callback, 1, callback_group=timer_cb_group)
        self.create_subscription(String, 'agent_process/result', self.vllm_result_callback, 1, callback_group=timer_cb_group)

        self.awake_client = self.create_client(SetBool, 'vocal_detect/enable_wakeup')
        self.awake_client.wait_for_service()
        self.set_model_client = self.create_client(SetModel, 'agent_process/set_model')
        self.set_model_client.wait_for_service()
        self.set_prompt_client = self.create_client(SetString, 'agent_process/set_prompt')
        self.set_prompt_client.wait_for_service()

        self.distance = 0
        self.distance_subscription = self.create_subscription(Int32,'sonar_controller/get_distance',self.distance_callback,1)

        self.timer = self.create_timer(0.0, self.init_process, callback_group=timer_cb_group)
        self.action_index = 0
        self.actions = []
        self.distances = []
        self.response_text = ""
        self.current_action = None
        self.action_start_time = None  # 记录动作开始时间
        self.is_moving_forward = False # 用于标记是否正在前进
        self._forward_twist = Twist()
        self._forward_twist.linear.x = 0.4
        self._last_cmd_pub = 0.0  # 上次重发 cmd_vel 的时间（看门狗保活）
        # 定时动作的状态机（P2-3.2）：不阻塞 process() 线程，由轮询推进
        self._action_deadline = None
        self._action_twist = None
        self._last_action_pub = 0.0
        self.all_actions_completed = False  # 标记所有动作是否完成
        self.initial_forward_executed = False # 初始前进是否执行过
        self.tts_speaking = False # Flag to indicate if TTS is currently speaking


    def get_node_state(self, request, response):
        return response

    def init_process(self):
        self.timer.cancel()


        msg = SetModel.Request()
        msg.model_type = 'vllm'
        if language == 'Chinese':
            msg.model = stepfun_vllm_model
            msg.api_key = stepfun_api_key
            msg.base_url = stepfun_base_url
        else:
            msg.api_key = vllm_api_key
            msg.base_url = vllm_base_url
            msg.model = vllm_model
        self.send_request(self.set_model_client, msg)

        msg = SetString.Request()
        msg.data = PROMPT
        self.send_request(self.set_prompt_client, msg)

        speech.play_audio(start_audio_path)
        threading.Thread(target=self.process, daemon=True).start()
        self.create_service(Empty, '~/init_finish', self.get_node_state)
        self.get_logger().info('\033[1;32m%s\033[0m' % 'start')
        self.get_logger().info('\033[1;32m%s\033[0m' % PROMPT)

    def send_request(self, client, msg):
        future = client.call_async(msg)
        while rclpy.ok():
            if future.done() and future.result():
                return future.result()

    def vllm_result_callback(self, msg):
        self.vllm_result = msg.data
        #self.get_logger().info(f"接收到的 vllm_result: {self.vllm_result}")

        try:
            '''
            json_string = self.vllm_result.strip()

            if json_string.startswith("```json"):
                json_string = json_string[6:]
            if json_string.endswith("```"):
                json_string = json_string[:-3]

            if json_string.startswith("{") and json_string.endswith("}"):
                pass
            else:
                json_string = json_string[1:-1]
            response = json.loads(json_string.strip())
            '''

            response = eval(self.vllm_result[self.vllm_result.find('{'):self.vllm_result.find('}')+1])

            if "action" in response and isinstance(response['action'], list):
                self.actions = response['action']
                self.distances = response.get('distance_one', [])
                self.response_text = response['response']
                self.action_index = 0 # reset
                self.stop_motion() # 停止所有运动
                self.is_moving_forward = False  # 重置前进状态
                self.all_actions_completed = False # 重置标志位
                self.initial_forward_executed = False # 重置初始前进标志
                # 取消可能还在进行的定时动作
                self._action_deadline = None
                self._action_twist = None

            else:
                self.get_logger().warn("JSON 响应中缺少 'action' 字段或不是列表，跳过动作执行。")
                self.actions = []
                self.distances = []
                self.response_text = ""
            msg = String()
            msg.data = response['response']
            self.tts_text_pub.publish(msg)
        except (ValueError, TypeError) as e:
            self.get_logger().error(f"JSON 解析错误: {str(e)}")
            self.get_logger().error(f"原始字符串：{self.vllm_result}")
            self.actions = []
            self.distances = []
            self.response_text = ""
            msg = SetBool.Request()
            msg.data = True
            self.send_request(self.awake_client, msg)

    def play_audio_finish_callback(self, msg):
        if msg.data:
            msg = SetBool.Request()
            msg.data = True
            self.send_request(self.awake_client, msg)
            self.tts_speaking = False  # TTS finished speaking

    def buzzer(self,count = 1):
        msg = BuzzerState()
        msg.freq = 1000
        msg.on_time = 0.2
        msg.off_time = 0.9
        msg.repeat = count
        self.buzzer_publisher.publish(msg)

    def process(self):
        while self.running:
            # 固定周期轮询，避免空闲时忙等占满 CPU（P2-3.1）
            time.sleep(self.PROCESS_PERIOD_SEC)
            self.keepalive_cmd_vel()

            # 定时动作期间只做推进，不再阻塞线程（P2-3.2）
            if self._action_deadline is not None:
                self._tick_timed_action()
                continue

            # 只有有 action 指令时才会执行
            if not (self.vllm_result and self.actions):
                continue

            current_distance = (self.distances[self.action_index]
                                if self.action_index < len(self.distances) else None)
            current_action = self.actions[self.action_index]

            # move_backward 不需要距离，直接执行
            if current_action == "move_backward()":
                self.stop_motion()
                self._begin_timed_action(current_action)
                continue

            if current_action == "move_forward()":
                if not self.is_moving_forward:
                    self.start_moving_forward()
                    self.is_moving_forward = True
                elif current_distance is not None and self.distance <= current_distance:
                    self.stop_motion()
                    self.is_moving_forward = False
                    self.get_logger().info(
                        f"距离 {self.distance} 小于等于 {current_distance}, 停止并执行下一个动作")
                    self._advance_action()
                continue

            # 其它动作：停车后按定时动作执行
            self.stop_motion()
            self._begin_timed_action(current_action)

    def _begin_timed_action(self, action_str):
        """开始一个定时动作（约 0.6s），立即返回，由 process() 周期推进。"""
        twist = self.execute_action(action_str)
        if twist is None:
            self._advance_action()
            return
        self._action_twist = twist
        self._last_action_pub = time.monotonic()
        self._action_deadline = self._last_action_pub + self.ACTION_DURATION_SEC
        self.get_logger().info(
            f"开始执行动作 {action_str} {self.ACTION_DURATION_SEC} 秒钟.")

    def _tick_timed_action(self):
        """按 10Hz 重发定时动作的速度，到点停车并进入下一个动作。"""
        now = time.monotonic()
        if (self._action_twist is not None
                and now - self._last_action_pub >= self.ACTION_REPUBLISH_SEC):
            self.mecanum_pub.publish(self._action_twist)
            self._last_action_pub = now
        if now >= self._action_deadline:
            self._action_deadline = None
            self._action_twist = None
            self.stop_motion()
            self._advance_action()

    def _advance_action(self):
        """推进到下一个动作；全部完成时触发完成回调。"""
        self.action_index += 1
        if self.action_index >= len(self.actions):
            self.all_actions_completed = True
            self.all_actions_completed_callback()

    def all_actions_completed_callback(self):
        """所有动作完成后执行的回调函数."""
        if not self.tts_speaking:  # 确保TTS没有在说话
            self.stop_motion()  # 停止所有运动
            self.get_logger().info("finish")
            self.get_logger().info(self.response_text)
            self.tts_speaking = True  # Set TTS speaking flag

            self.vllm_result = ''  # 清空结果
            self.all_actions_completed = False  # 重置所有动作已完成标志
            self.action_index = 0  # 重置action index，以便下次正常运行
            self.initial_forward_executed = False
            # 清掉可能残留的定时动作状态
            self._action_deadline = None
            self._action_twist = None

            msg = SetBool.Request()
            msg.data = True
            self.send_request(self.awake_client, msg)



    def keepalive_cmd_vel(self):
        """前进期间以 10Hz 重发速度，配合底盘 cmd_vel 看门狗。"""
        if not self.is_moving_forward:
            return
        now = time.monotonic()
        if now - self._last_cmd_pub >= 0.1:
            self.mecanum_pub.publish(self._forward_twist)
            self._last_cmd_pub = now

    def start_moving_forward(self):
        """开始前进."""
        self.mecanum_pub.publish(self._forward_twist)
        self._last_cmd_pub = time.monotonic()
        self.is_moving_forward = True
        self.get_logger().info("开始前进")

    def stop_motion(self):
        """停止所有运动."""
        twist = Twist()
        twist.linear.x = 0.0
        twist.angular.z = 0.0
        self.mecanum_pub.publish(twist)
        self.get_logger().info("停止运动")
        self.is_moving_forward = False
        self.current_action = None

    def execute_action(self, action_str):
        twist = Twist()
        if action_str == "move_forward()":
            twist.linear.x = 0.4
            self.get_logger().info("前进")
        elif action_str == "move_backward()":
            twist.linear.x = 0.0
            self.get_logger().info("后退")
        elif action_str == "turn_left()":
            twist.angular.z = 4.0
            self.get_logger().info("左转")
        elif action_str == "turn_right()":
            twist.angular.z = -4.0
            self.get_logger().info("右转")
        elif action_str == "move_left()":
            twist.linear.y = 2.5
            self.get_logger().info("左移")
        elif action_str == "move_right()":
            twist.linear.y = -2.5
            self.get_logger().info("右移")
        elif action_str == "move_forward_fast()":
            twist.linear.x = 0.7
            self.get_logger().info("快速前进")
        elif action_str == "move_forward_slow()":
            twist.linear.x = 0.3
            self.get_logger().info("缓慢前进")
        elif action_str == "buzzer()":
            self.buzzer()
            self.get_logger().info('蜂鸣')
        else:
            self.get_logger().warn(f"未知动作: {action_str}")
            return None

        self.mecanum_pub.publish(twist)
        self.get_logger().info(f"正在执行动作: {action_str}")
        self.current_action = action_str
        return twist


    def distance_callback(self, msg):
        self.distance = msg.data
        # self.get_logger().info(f"当前距离: {self.distance} mm")

def main():
    node = VLLMTrack('vllm_track')
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    executor.spin()
    node.destroy_node()

if __name__ == "__main__":
    main()
