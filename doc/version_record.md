# 版本记录

## 版本说明
本项目遵循语义化版本 SemVer：`a.b.b`
- 主版本号（a）：不兼容的重大改动、架构重构等
- 次版本号（b）：相对独立的小功能完成，但是向下兼容
- 小版本号（c）：bug修复、小幅优化、协议变更

-------
> 特别说明:
>
> 1.日常开发commit不修改版本号，仅在正式Release发布时升级版本。
> 
> 2.app和设备端的通信协议变化时，版本号一定要修改，即app和设备端的版本“a.b.c”永远保存一致，如果不一致，可能会导致连接不上设备，或者相关命令无效等问题。

-------
## 提交规范
commit 格式：`type(scope)(client): description`
type：必填
  - 新增功能：feat
  - 缺陷修复：fix
  - 文档变更：docs
  - 重构代码：refactor
  - 代码格式调整：style
  - 性能优化：perf
  - 增加 / 修改测试代码：test
  - 构建、CI 配置、依赖更新：chore
  - 发布新版本：release
scope：必填
  - mqtt：mqtt协议
  - webrtc：webrtc协议
  注意：scope项写啥可以，只是代表修改的范围，和type没有关系。
client：可选
  - ios：只涉及到ios代码的修改
  - android：只涉及到android代码的修改
  - web：只涉及到web代码的修改
  - device：只涉及到设备端代码的修改
  - all：所有端的代码都涉及到修改
  注意，没有修改代码时，client项可以不写。

不可以在main分支提交，修改代码请先提交至 dev 分支，CI通过后，才能合并到main分支。

示例1（单行，一句话描述清楚）：
```
feat(mqtt): 新增设备在线状态上报

fix(webrtc): 修复视频流偶发断连问题
```

示例2（多行，追加详细描述）：
```
fix(rtc): 修复弱网下推流超时

1)调整ffmpeg参数，降低延迟，优化重连逻辑
2)新增视频流超时重连机制

```

## main分支保护
github服务器开启了main分支保护和CI，不能在main分支上push代码，只能先在dev分支上进行开发，push之后服务器会进行代码检查，检查通过后，才能合并到main分支。

```bash
# 1. 本地 dev 开发完，推到远端
git checkout dev
git push origin dev

# 2. 在 GitHub 上开 PR：base = main，compare = dev
#    https://github.com/gong-shuang/walle-ai/compare/main...dev

# 3. 等 PR 上的 lint / build-test 变绿，点 Merge pull request

# 4. 本地同步 main
git checkout main
git pull origin main

# 5. 让 dev 与 main 保持一致（二选一）
git checkout dev
git merge main          # 保留合并记录
```



## 主要版本变更记录

备注：只记录“a.b.c”，不记录“d”，想查看详细的版本记录，需要查看git log日志。

|| 日期 || 版本号 || 描述  ||
|| --- || --- || --- || --- ||
|| 2023-08-01 || 0.1.1 || 初始demo完成，功能较完整，有查看设备状态，视频监控，语音通话，执行命令等功能 ||

