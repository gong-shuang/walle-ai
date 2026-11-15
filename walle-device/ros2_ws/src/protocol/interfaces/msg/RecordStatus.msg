# 录像状态（record_video_node 以 transient_local 发布）
bool recording
string filename
string started_at
float64 started_ts
int64 current_duration_sec   # 当前分片已录制时长（秒）
int64 total_duration_sec     # 本次会话累计时长（含已结束分片，秒）
int64 current_size_bytes     # 当前分片文件大小（字节）
int64 used_bytes             # 录像目录已用空间（字节）
int64 limit_bytes            # 录像目录空间上限（字节）
