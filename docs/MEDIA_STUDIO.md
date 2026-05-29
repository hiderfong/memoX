# Media Studio

MemoX 的媒体创作工作台面向长期使用的 I2V 和视频编辑场景。它保留聊天和文档预览中的单次“图生视频”入口，同时为批量创作提供独立页面、后台任务、作品库、失败重试和队列治理。

## User Flows

- `/media` 页面提供“批量图生视频”和“视频编辑”两个工作区。
- 批量图生视频最多一次提交 8 个素材，每个素材需要图片 URL 或 `/api/files/{name}`，以及独立 Prompt、时长、分辨率和 negative prompt。
- 视频编辑支持视频 URL 或 `/api/files/{name}`，参考图片列表、分辨率、画幅、时长、音频设置、seed、prompt 扩写和水印参数。
- 作品库展示所有生成/编辑记录，可按任务类型和状态筛选，并支持打开、复制链接、删除记录和失败重试。

## Background Jobs

产品化工作台默认使用后台任务接口，而不是同步等待模型返回：

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/videos/i2v/jobs` | 提交单条 I2V 后台任务 |
| `POST` | `/api/videos/i2v/batch/jobs` | 批量提交 I2V 后台任务 |
| `POST` | `/api/videos/edit/jobs` | 提交视频编辑后台任务 |
| `GET` | `/api/videos/assets` | 查询作品库和任务状态 |
| `GET` | `/api/videos/assets/{asset_id}` | 查询单条作品/任务 |
| `POST` | `/api/videos/assets/{asset_id}/retry` | 重试失败任务 |
| `GET` | `/api/videos/jobs/status` | 查询运行槽位和等待队列 |

后台任务先创建 `media_assets` 记录并返回 `queued` 状态。执行时状态变为 `running`，成功后写入 `url/input_mode` 并变为 `success`，失败后写入 `error` 并变为 `failed`。

当前进程内调度器默认最多同时运行 2 个媒体任务。额外任务会等待运行槽位释放，前端作品库会轮询 `/api/videos/jobs/status` 和 `/api/videos/assets` 展示运行槽位、等待执行、持久化排队记录和运行记录。

## Recovery And Retry

服务启动时会扫描上次进程遗留的 `queued` / `running` 媒体记录，把它们标记为 `failed`，错误信息提示用户可以重试。这样用户不会长期看到无法推进的卡住状态。

重试失败任务时，系统复用原始素材、Prompt 和参数，保留同一个 `asset_id`，并在 `parameters` 中维护：

- `retry_count`
- `last_retry_at`
- `interrupted_status` 和 `interrupted_at`，仅当任务由启动恢复流程标记失败时出现

运行中的任务不能重试，接口会返回 `409`。

## Operational Checks

- 确认 `image_to_video.enabled=true` 时 `DASHSCOPE_API_KEY` 可用。
- 确认 `MEMOX_FILE_SIGNING_SECRET` 已设置，以便外部模型服务能安全访问本地上传素材的短期签名 URL。
- 使用 `/api/videos/jobs/status` 观察运行槽位和等待队列。
- 使用 `/api/videos/assets?status=failed` 查看失败任务和错误原因。
- 推荐发布前运行：

```bash
.venv/bin/python -m pytest tests/test_i2v_api.py tests/test_i2v_client.py -q
npm run build
```
