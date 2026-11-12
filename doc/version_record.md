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
commit 格式：`type(scope): description`
- feat: 新功能
- fix: 缺陷修复
- docs: 文档变更（README、说明）
- refactor: 重构代码，不新增功能、不修复 bug
- style: 代码格式调整，无逻辑改动
- perf：性能优化
- test：增加 / 修改测试代码
- chore: 构建、CI 配置、依赖更新
- release: 发布新版本
  所有合并请求（PR）请提交至 dev 分支。

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

