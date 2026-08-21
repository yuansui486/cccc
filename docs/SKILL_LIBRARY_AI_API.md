# Skill Library AI 维护 API

## 接入信息

- Base URL: `https://dongdongkc.shierkeji.com:5205/onecolleague_agent/api/v1/skill-library`
- AI 鉴权: `Authorization: Bearer TOKEN`
- 请求/响应: JSON；ZIP 与图标上传使用 `multipart/form-data`
- 服务令牌由管理员在 Skill 管理页创建，支持命名、过期时间和撤销。明文只显示一次。

服务令牌权限为 `skills:read`、`skills:write`、`skills:delete`。管理员 JWT 也可调用管理接口，但 AI 应使用独立服务令牌。

## 分组管理

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/admin/categories` | 查看全部分组及 Skill 数量 |
| `POST` | `/admin/categories` | 新增自由分组 |
| `PATCH` | `/admin/categories/{id_or_slug}` | 修改名称、slug 或排序 |
| `DELETE` | `/admin/categories/{id_or_slug}` | 删除空分组 |
| `PUT` | `/admin/categories/order` | 整体调整分组顺序 |

```bash
curl -X POST "$BASE/admin/categories" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"办公自动化","slug":"office-automation","display_order":10}'

curl -X PUT "$BASE/admin/categories/order" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"category_ids":[3,1,2]}'
```

分组仍被 Skill 引用时，删除返回 `409 category_in_use`，响应中包含引用它的 Skill。

## Skill 查询与维护

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/admin/capabilities` | 分页查看、搜索 Skill |
| `GET` | `/admin/capabilities/{ref}` | 查看单个 Skill |
| `POST` | `/admin/capabilities` | 创建草稿 Skill |
| `PATCH` | `/admin/capabilities/{ref}` | 更新名称、说明、标签、分组和排序 |
| `DELETE` | `/admin/capabilities/{ref}` | 物理删除 Skill |
| `POST` | `/admin/capabilities/{ref}/icon` | 上传并替换图标 |
| `DELETE` | `/admin/capabilities/{ref}/icon` | 移除图标 |

`ref` 可使用数据库 ID、slug 或 capability ID。删除立即生效；已发布 Skill 会保留最小 tombstone，供 CCCC 增量同步删除状态。

```bash
curl -X PATCH "$BASE/admin/capabilities/SKILL_REF" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"网页研究","category_id":3,"display_order":20,"tags":["research","browser"]}'

curl -X DELETE "$BASE/admin/capabilities/SKILL_REF" \
  -H "Authorization: Bearer $TOKEN"
```

## 原子创建并发布

推荐 AI 使用 `POST /admin/skills/publish`。它在一次事务内创建或更新 Skill、校验 ZIP、创建版本并发布；失败时数据库状态整体回滚。同一 slug 配合新版本号可直接发布下一版本。

字段：

- 必填: `slug`、`name`、`version`、ZIP 文件 `file`
- 可选: `description_short`、`category_id`、`display_order`、`tags`、`risk_tags`、`changelog`、图片文件 `icon`
- `tags` 与 `risk_tags` 使用逗号分隔
- ZIP 必须包含 `SKILL.md`

```bash
curl -X POST "$BASE/admin/skills/publish" \
  -H "Authorization: Bearer $TOKEN" \
  -F 'slug=browser-research' \
  -F 'name=Browser Research' \
  -F 'version=1.2.0' \
  -F 'description_short=检索并整理网页资料' \
  -F 'category_id=3' \
  -F 'display_order=20' \
  -F 'tags=research,browser' \
  -F 'changelog=增加引用检查' \
  -F 'file=@SKILL_PACKAGE.zip;type=application/zip' \
  -F 'icon=@ICON.png;type=image/png'
```

## 图标规范

支持 PNG、JPEG、WebP，最大 2 MB，宽高均须在 64 到 2048 像素。服务端统一生成 256x256 WebP，并以 SHA-256 不可变 URL 发布：`/icons/{sha256}.webp`。

## 版本生命周期

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/admin/capabilities/{ref}/versions` | 查看全部版本 |
| `POST` | `/admin/capabilities/{ref}/versions/package` | 上传 ZIP 草稿版本 |
| `PATCH` | `/admin/versions/{version_id}` | 修改草稿版本元数据 |
| `DELETE` | `/admin/versions/{version_id}` | 删除草稿版本 |
| `POST` | `/admin/versions/{version_id}/validate` | 校验版本 |
| `POST` | `/admin/versions/{version_id}/publish` | 发布版本 |
| `POST` | `/admin/versions/{version_id}/deprecate` | 标记废弃 |
| `POST` | `/admin/versions/{version_id}/revoke` | 撤回版本 |

分类、图标和排序变更会立即更新公开 record 和增量时间，不要求创建内容版本。

## Python 示例

```python
import os
import requests

BASE = os.environ["SKILL_API_BASE"]
TOKEN = os.environ["SKILL_TOKEN"]
headers = {"Authorization": f"Bearer {TOKEN}"}

skills = requests.get(f"{BASE}/admin/capabilities", headers=headers, timeout=30)
skills.raise_for_status()

with open("SKILL_PACKAGE.zip", "rb") as package:
    response = requests.post(
        f"{BASE}/admin/skills/publish",
        headers=headers,
        data={"slug": "browser-research", "name": "Browser Research", "version": "1.2.0"},
        files={"file": ("SKILL_PACKAGE.zip", package, "application/zip")},
        timeout=120,
    )
response.raise_for_status()
print(response.json())
```

## 错误与安全

- `400`: 字段、ZIP 或图标无效
- `401`: TOKEN 缺失、过期或已撤销
- `403`: TOKEN 缺少对应权限
- `404`: Skill、分组或版本不存在
- `409`: slug/版本冲突，或分组仍被引用

不要在日志、提示词或代码仓库中记录 TOKEN、管理员 JWT、ZIP 内容或一次性令牌明文。
