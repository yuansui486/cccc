# 一号同事相对 CCCC 的迁移保留清单

本文用于后续把最新版 `CCCC` 合并进当前 `OneColleague` 分支时，逐项确认哪些一号同事定制必须保留。

这不是完整 changelog，而是面向 merge 的保留文档。

## 1. 基线与范围

- 基线分支：`main`
- 上轮 refresh 基线提交：`de07912a32415f0661f91cd8fceb4f92416d4c02`
- 当前需要吸收的 main 提交上界：`cea6324bcd55a4bea4e7fc1ac412aa2ac2bb177d`
- 当前分支：`OneColleague`
- 当前 HEAD：`79281d5`
- 当前 merge-base：`cea6324bcd55a4bea4e7fc1ac412aa2ac2bb177d`
- 本轮主线增量范围：`de07912..cea6324`
- 一号同事保留集对照仍以 `main...OneColleague` 为准；本轮完成后，后续 refresh 不应再把 `de07912` 当成主线最新真相。

这段范围内，一号同事定制的主提交为：

- `5cf4faf feat(web): add done-hub access and onecolleague branding`
- `ee4727e feat(web): simplify onecolleague account access`
- `74db549 feat(web): polish onecolleague account ui`
- `9d7bc18 feat(web): refine account access ui`
- `3d3c9ed feat(done-hub): provision local client configs`
- `68abef9 fix(web): align im bridge locale copy`
- `ee9d541 style(web): shift UI theme to light blue`
- `79b320f merge(main): refresh OneColleague onto c921877`
- `09ba6da docs: refresh onecolleague merge checklist`
- `018fa54 merge(main): refresh OneColleague onto de07912`
- `d51bfc8 docs: refresh onecolleague merge checklist`
- `12e7efc fix(web): align group settings default tab with main`
- `df7cefc revert(web): rollback onecolleague branding aliasing`
- `d6df847 style(web): trim new group button`
- `79281d5 merge(main): refresh OneColleague onto cea6324`

后续 merge 最新 CCCC 时，本文只关注“一号同事相对原版 CCCC 的必保留行为”，不关注上游普通演进本身。

### 1.1 本轮（3/18）额外吸收框架

这轮不能只按上面的 preserve 清单机械 merge，还必须额外吸收 `c921877..de07912` 这段主线新结构。当前 main 侧新增热点主要是：

- `desktop/` 旧实现删除，迁到 Web 内的 `webPet` 方案
- Web runtime / remote access / shutdown / health endpoint 加固

因此本轮 merge 的正确口径不是“恢复旧的 desktop pet 形态”，而是：

- 在新 main 的 `WebPet + web runtime/apply/restart/shutdown + /api/v1/health` 结构上
- 同时保住一号同事现有的 DoneHub gate / 账户链 / 品牌 / locale 口径

本轮 changed-in-both 的高风险集合主要是：

- `src/cccc/ports/web/app.py`
- `src/cccc/ports/web/schemas.py`
- `web/src/App.tsx`
- `web/src/components/AgentTab.tsx`
- `web/src/components/AppModals.tsx`
- `web/src/components/MessageBubble.tsx`
- `web/src/components/layout/GroupSidebar.tsx`
- `web/src/stores/index.ts`
- `web/src/types.ts`
- `web/src/i18n/locales/{en,ja,zh}/{modals,settings}.json`

这组文件在本轮要按“先吃新 main 结构，再回填一号同事行为链”处理，不能整文件选边。

### 1.2 本轮（3/24）额外吸收框架

这轮额外吸收的是 `de07912..cea6324`，重点不是旧的 desktop/webPet 收口，而是新主线继续推进的 Web shell / Presentation / Branding / IM 扩展：

- `f473bea` 把 app shell 重新拆回 `AppShell` / hooks 结构
- `0c57005` 到 `8c3d29b` 引入并扩展 Presentation surface / browser flow
- `fa719fa` 新增 branding controls 并继续打磨 console UX
- `151d804` / `wecom` 相关提交把 IM bridge 能力扩到企业微信

这轮 changed-in-both 的高风险集合主要是：

- `src/cccc/ports/web/app.py`
- `src/cccc/ports/web/schemas.py`
- `web/src/App.tsx`
- `web/src/components/AppModals.tsx`
- `web/src/components/AuthGate.tsx`
- `web/src/components/SettingsModal.tsx`
- `web/src/components/layout/AppHeader.tsx`
- `web/src/components/layout/GroupSidebar.tsx`
- `web/src/components/layout/MobileMenuSheet.tsx`
- `web/src/pages/chat/ChatComposer.tsx`
- `web/src/stores/index.ts`
- `web/src/types.ts`
- `web/src/i18n/locales/{en,ja,zh}/{chat,layout,modals,settings}.json`

这组文件在本轮要继续按“先吃新 main 结构，再回填一号同事 preserve 行为链”处理，尤其不能把新的 branding/presentation shell 回退成旧的一体式 App，也不能让一号同事的登录门禁、品牌壳层、DoneHub 账户入口和 settings 裁剪在新 shell 里丢失。

## 2. 必须保留的品牌与用户可见裁剪

### 2.1 品牌源头与浏览器壳层

以下文件共同决定“一号同事”品牌源头，不能只保一部分：

- `web/src/utils/displayText.ts`
- `web/public/onecolleague-logo.svg`
- `web/public/onecolleague-logo.png`
- `web/index.html`
- `web/public/manifest.webmanifest`

必须保留的行为：

- 品牌名统一由 `getAppBrandName()` 返回“一号同事”。
- 品牌 logo 统一由 `getAppLogoPath()` 返回 `/ui/onecolleague-logo.svg`。
- 浏览器标签页标题、favicon、apple-touch-icon、PWA manifest 名称与图标都保持一号同事。
- 浏览器壳层主题色保持浅蓝系：`theme-color = #7dd3fc`。
- PWA manifest 背景色保持当前深蓝值：`background_color = #0b1530`。

merge 风险：

- 若上游改动 `index.html`、manifest 或 logo 引用路径，最容易回退到原始 CCCC 壳层。
- 若组件重新写死品牌名/图标路径，会让后续再升级时更容易漏改。

### 2.2 App 入口改为一号同事登录门禁

关键文件：

- `web/src/App.tsx`
- `web/src/components/DoneHubLoginGate.tsx`
- `web/src/stores/useDoneHubStore.ts`

必须保留的行为：

- App 初始化后，未连通一号同事账号时，不进入原始主界面，而是先显示 `DoneHubLoginGate`。
- 登录页是极简账号密码表单，不再暴露可编辑 base URL。
- 登录页品牌头必须在登录框外部上方，不是卡片内部。
- 登录页继续读取并预填已保存的账号信息。

merge 风险：

- 上游若改 `App.tsx` 初始化链，最容易把 `DoneHubLoginGate` 条件入口丢掉。
- 上游若改登录页结构，最容易把品牌头重新塞回卡片内部。

### 2.3 侧栏、顶部和移动端品牌入口

关键文件：

- `web/src/components/layout/GroupSidebar.tsx`
- `web/src/components/layout/AppHeader.tsx`
- `web/src/components/layout/MobileMenuSheet.tsx`
- `web/src/components/AppModals.tsx`
- `web/src/stores/useModalStore.ts`

必须保留的行为：

- 侧栏品牌区使用一号同事 logo 和品牌名，不回退到原始 `CCCC` 或旧 logo。
- 桌面右上角保留账户入口，显示余额。
- `doneHub.group === "pro"` 时，桌面右上角显示金色 `PRO` 标识。
- 移动端菜单里也保留账户入口、余额和 `PRO` 标识。
- 桌面和移动端账户入口都能打开 `DoneHubAuthModal`。

merge 风险：

- 这是一条 `App -> Header/Mobile -> ModalStore -> AppModals -> DoneHubAuthModal` 的整链路，不能只保单个组件。
- 若 `group`、`quota`、`status` 透传链断掉，余额入口和 `PRO` 标识会一起失效。

### 2.4 账户弹层动作区定型

关键文件：

- `web/src/components/modals/DoneHubAuthModal.tsx`
- `web/src/i18n/locales/zh/modals.json`
- `web/src/i18n/locales/en/modals.json`
- `web/src/i18n/locales/ja/modals.json`

必须保留的行为：

- 已登录状态下展示账号、余额、已用额度。
- 底部动作区固定为三项：
  - `跳转至算力中心`
  - `刷新余额`
  - `退出登录`
- “跳转至算力中心”按钮继续保留清晰但不过度突兀的边框与轻强调阴影。
- 外链仍指向 `https://peer.shierkeji.com/panel/dashboard`。
- 危险动作文案必须是“退出登录”，不能回退成“断开当前连接”。

### 2.5 设置页导航裁剪

关键文件：

- `web/src/components/SettingsModal.tsx`

必须保留的行为：

- Global 只保留：
  - `actorProfiles`
  - `myProfiles`
- Group 只保留：
  - `guidance`
  - `automation`
  - `im`
  - `blueprint`
- 以下入口和对应内容分支保持隐藏：
  - `delivery`
  - `space` / Notebook
  - `messaging`
  - `transcript`
  - `capabilities`
  - `webAccess`
  - `developer`
- `globalTabs` 和 `groupTabs` 的 fallback 逻辑必须继续存在，避免 active tab 落到空白页。

merge 风险：

- 只恢复 tab 列表或只恢复内容分支中的任一处，都会把隐藏模块重新暴露出来。

### 2.6 Guidance 页裁剪为 Actor Notes

关键文件：

- `web/src/components/modals/settings/GuidanceTab.tsx`

必须保留的行为：

- Guidance 页面最终只展示 actor 维度的提示词编辑。
- 左侧作用域列表只基于 `visibleActorScopes`。
- 页面仍然只渲染 `renderHelpCard()` 这条路径，不恢复 preamble/common/foreman/peer 可见区块。

merge 风险：

- 该文件里仍有一些未走当前渲染路径的旧结构；merge 时不能因为这些残留把旧版 return 结构带回来。

### 2.7 IM Bridge 平台中文展示

关键文件：

- `web/src/components/modals/settings/IMBridgeTab.tsx`
- `web/src/i18n/locales/zh/settings.json`
- `web/src/i18n/locales/en/settings.json`
- `web/src/i18n/locales/ja/settings.json`

必须保留的行为：

- 平台下拉中继续显示 `飞书`、`钉钉`。
- 状态卡片展示平台时，继续通过映射函数把 `feishu` / `dingtalk` 转成人类可读中文，不直接显示原始 token。
- 中文 locale 中和飞书/钉钉相关的说明继续保持当前写法，不回退到混用 `Lark` 的旧文案。
- 英文和日文 locale 里也继续统一使用 `Feishu` 命名：
  - `description` 不回退成 `Feishu/Lark`
  - `larkGlobal` 标签保持 `Feishu (Global)` / `Feishu (グローバル)`
  - region hint 继续强调“Feishu 在不同区域使用不同域名”，而不是“Feishu/Lark 共享 API”

### 2.8 品牌配色与通用文案收口

关键文件：

- `web/src/index.css`
- `web/src/components/AuthGate.tsx`
- `web/src/services/doneHub.ts`
- `web/src/i18n/locales/zh/common.json`
- `web/src/i18n/locales/zh/layout.json`
- `web/src/i18n/locales/zh/modals.json`
- `web/src/i18n/locales/en/common.json`
- `web/src/i18n/locales/en/layout.json`
- `web/src/i18n/locales/en/modals.json`
- `web/src/i18n/locales/ja/common.json`
- `web/src/i18n/locales/ja/layout.json`
- `web/src/i18n/locales/ja/modals.json`

必须保留的行为：

- 全局主色和氛围色仍是当前的一号同事浅蓝/天空蓝体系，不回退到更早的绿色主导方案，也不回退到原始 CCCC 风格。
- `common.appName` 在中英日都保持为“一号同事”。
- 账户入口、余额文案、退出登录、算力中心等相关文案继续保留当前多语言收口。
- `formatDoneHubQuota()` 继续统一保留两位小数。
- `sanitizeDoneHubErrorMessage()` 继续把涉及本地文件和路径的报错收敛成产品化文案，不向用户暴露 `.codex`、`.gemini`、`auth.json`、`config.toml`、`settings.json`、`USERPROFILE`、`Path.home()` 等细节。
- Header、Mobile、Settings、Guidance 等高频交互入口的强调色继续使用当前 sky/blue token，不回退到早期 emerald token。

## 3. 必须保留的 DoneHub/一号同事账号接入

### 3.1 后端 BFF 路由与 schema

关键文件：

- `src/cccc/ports/web/app.py`
- `src/cccc/ports/web/routes/done_hub.py`
- `src/cccc/ports/web/schemas.py`

必须保留的行为：

- Web 后端继续注册 done-hub 相关路由。
- `DoneHubLoginRequest` 和 `DoneHubSelfRequest` schema 继续存在。
- 前端不直接调用 done-hub 远端接口，而是继续通过当前 Web 后端 BFF 代理。

### 3.2 `/login` 单次 provisioning，`/self` 纯刷新

关键文件：

- `src/cccc/ports/web/routes/done_hub.py`
- `web/src/stores/useDoneHubStore.ts`
- `web/src/App.tsx`

必须保留的行为：

- `/api/v1/done_hub/login`
  - 调用 `POST /api/user/login`
  - 再调用 `GET /api/user/self`
  - 生成 session
  - 仅在这里执行本地客户端 provisioning
- `/api/v1/done_hub/self`
  - 仅使用 Bearer token 刷新 session
  - 不做 token 创建、模型选择或写盘

这是整个接入线里最重要的约束。

原因：

- 前端初始化恢复登录态时会调用 `/self`。
- 前端连接成功后还会每 60 秒自动刷新一次 `/self`。
- 如果把 provisioning 挂进 `/self`，就会在恢复登录和定时刷新时反复创建 token、反复写本地配置。

### 3.3 `pro` / 普通用户分流

关键文件：

- `src/cccc/ports/web/routes/done_hub.py`
- `web/src/types.ts`
- `web/src/App.tsx`
- `web/src/components/AppModals.tsx`
- `web/src/components/layout/AppHeader.tsx`
- `web/src/components/layout/MobileMenuSheet.tsx`

必须保留的行为：

- 用户分流来源继续是 `GET /api/user/self.data.group`。
- `group == "pro"` 时：
  - 后端跳过 `.codex` / `.gemini` 本地配置写入。
  - 前端顶部和移动端继续显示金色 `PRO` 标识。
- 非 `pro` 用户时：
  - 继续执行 token 检查、模型选择、本地配置写入。

### 3.4 token 列表查找与补建规则

关键文件：

- `src/cccc/ports/web/routes/done_hub.py`

必须保留的行为：

- 获取 token 列表时必须按分页拉取，直到取完整个 `total_count`。
- 查找 token 时按 `name` 精确匹配。
- 存在多条同名 token 时，按最大 `id` 取最新一条。
- 如果不存在 `codex` 或 `gemini`：
  - 先 `POST /api/token/` 创建
  - 再重新 list 一次
  - 从重新查询结果中取得真实 `key`

不能退化成的错误实现：

- 只查第一页。
- 创建后假设接口直接回传 `key`。
- 对同名 token 随机取一条而不是取最新一条。

### 3.5 模型选择规则

关键文件：

- `src/cccc/ports/web/routes/done_hub.py`

必须保留的行为：

- 调用 `GET https://peer.shierkeji.com/api/available_model`
- 从返回的 `data` 对象中：
  - 取第一个以 `gpt` 开头的模型名用于 Codex
  - 取第一个以 `gemini` 开头的模型名用于 Gemini
- 这里不能写死具体模型名，如 `gpt-5.4` 或 `gemini-3.1-pro-preview`。

### 3.6 Windows 语义下的本地配置写入

关键文件：

- `src/cccc/ports/web/routes/done_hub.py`
- `tests/test_web_done_hub_routes.py`

用户需求明确要求以 Windows 用户目录语义理解 `%USERPROFILE%`。

当前实现保留的目标文件含义是：

- Codex:
  - `%USERPROFILE%\\.codex\\auth.json`
  - `%USERPROFILE%\\.codex\\config.toml`
- Gemini:
  - `%USERPROFILE%\\.gemini\\.env`
  - `%USERPROFILE%\\.gemini\\settings.json`

当前服务端代码通过 `Path.home()` 写入当前用户 home 目录；后续若运行环境仍面向 Windows 用户，这一语义必须继续与 `%USERPROFILE%` 对齐。

必须保留的文件内容规则：

- `auth.json`
  - 仅保留 `OPENAI_API_KEY`
  - 值必须是 `sk-<raw key>`
- `config.toml`
  - 头部模板必须保持当前格式
  - `model_provider = "custom"`
  - `wire_api = "responses"`
  - `requires_openai_auth = true`
  - `base_url = "https://peer.shierkeji.com/v1"`
- `.gemini/.env`
  - 3 行固定顺序
  - 不加引号
- `.gemini/settings.json`
  - 固定为 gemini-api-key 认证配置

### 3.7 `config.toml` 只能替换 `[projects` 前面的前缀

关键文件：

- `src/cccc/ports/web/routes/done_hub.py`
- `tests/test_web_done_hub_routes.py`

这是 merge 时必须逐字确认的点。

必须保留的行为：

- 只替换 `config.toml` 中首个 `[projects` 之前的内容。
- 第一个 `[projects` 以及后面的内容全部原样保留。
- 若原文件中没有 `[projects`，则整文件写入新的 codex 前缀。

不能退化成的错误实现：

- 用 TOML parse/rewrite 整个文件，导致 `[projects.'...']`、`[windows]`、路径转义或空行风格被重排。
- 每次登录都重复堆叠一份新的 `[model_providers.custom]` 段。

### 3.8 Store、gate、header、mobile、modal 的前端承接链

关键文件：

- `web/src/types.ts`
- `web/src/stores/index.ts`
- `web/src/stores/useDoneHubStore.ts`
- `web/src/App.tsx`
- `web/src/components/AppModals.tsx`
- `web/src/stores/useModalStore.ts`
- `web/src/components/DoneHubLoginGate.tsx`
- `web/src/components/modals/DoneHubAuthModal.tsx`
- `web/src/components/layout/AppHeader.tsx`
- `web/src/components/layout/MobileMenuSheet.tsx`

必须保留的行为：

- `useDoneHubStore` 继续承担：
  - session 恢复
  - remembered login 恢复
  - 登录
  - 刷新
  - 退出
- `DoneHubSession` 继续包含 `group` 字段。
- `App.tsx` 继续在初始化时触发 `initializeDoneHub()`。
- 已连接状态下继续每 60 秒 `refreshDoneHub()`。
- `doneHub` 对象继续透传到 header 和 mobile。
- modal state 中继续存在 `doneHubAuth`。

### 3.9 错误文案边界

关键文件：

- `src/cccc/ports/web/routes/done_hub.py`
- `web/src/services/doneHub.ts`
- `web/src/stores/useDoneHubStore.ts`

必须保留的行为：

- 后端写盘失败时统一返回：
  - `code = done_hub_client_config_failed`
  - `message = 登录成功，但本机客户端配置写入失败，请稍后重试或联系管理员。`
- 前端 sanitizer 继续兜底，避免原始路径或文件名透出到登录页和账户弹层。

## 4. 支撑性文件与文案适配

以下文件不一定都是核心功能入口，但当前承担了一号同事的名称、收件人、状态、时间等显示收口，merge 时不能随意回退：

- `web/src/components/MessageBubble.tsx`
- `web/src/components/SearchModal.tsx`
- `web/src/components/VirtualMessageList.tsx`
- `web/src/hooks/useActorDisplayName.ts`
- `web/src/pages/chat/ChatComposer.tsx`
- `web/src/utils/groupStatus.ts`
- `web/src/utils/time.ts`
- `web/src/utils/displayText.ts`
- `web/src/i18n/locales/zh/chat.json`
- `web/src/i18n/locales/zh/common.json`
- `web/src/i18n/locales/zh/layout.json`
- `web/src/i18n/locales/zh/modals.json`
- `web/src/i18n/locales/zh/settings.json`
- `web/src/i18n/locales/en/chat.json`
- `web/src/i18n/locales/en/common.json`
- `web/src/i18n/locales/en/layout.json`
- `web/src/i18n/locales/en/modals.json`
- `web/src/i18n/locales/en/settings.json`
- `web/src/i18n/locales/ja/chat.json`
- `web/src/i18n/locales/ja/common.json`
- `web/src/i18n/locales/ja/layout.json`
- `web/src/i18n/locales/ja/modals.json`
- `web/src/i18n/locales/ja/settings.json`

至少要继续保住这些结果：

- `common.appName` 为“一号同事”
- 收件人显示使用当前中文化标签，而不是退回原始 token 或旧英文标签
- 群组状态、时间相关展示继续走当前 helper/i18n 收口
- done-hub 相关新增文案在中英日三套 locale 保持同步
- IM Bridge 的英文/日文文案继续统一使用 `Feishu` 命名，不重新混入 `Feishu/Lark` 或 `Lark (Global)` 旧写法

## 5. 测试锚点

关键文件：

- `tests/test_web_done_hub_routes.py`

这份测试文件是后续 merge 时最重要的后端回归锚点，必须保留。

当前覆盖的核心场景：

- 非法 base URL 被拒绝
- `/login` 正确串联 `POST /api/user/login -> GET /api/user/self`
- `/self` 仅用 Bearer token 刷新
- 普通用户生成 `.codex` / `.gemini` 配置
- `pro` 用户完全跳过本地配置写入
- token 分页查询时不重复创建
- `config.toml` 前缀替换时保留原有 `[projects...]` / `[windows]` tail
- 写盘失败时返回泛化错误，且不泄露路径

说明：

- 当前没有覆盖品牌/UI 收口线的前端自动化测试，所以这些点仍要靠 merge checklist 做人工核对。

## 6. 合并最新版 CCCC 时的回灌检查表

建议后续 merge 完上游后，至少逐项检查以下内容：

### 6.1 品牌与入口

- [ ] `web/index.html` 仍是“一号同事”标题与一号同事图标
- [ ] `web/public/manifest.webmanifest` 仍是“一号同事”名称与一号同事图标
- [ ] `web/index.html` 和 `web/public/manifest.webmanifest` 的主题色仍是 `#7dd3fc`
- [ ] `web/src/utils/displayText.ts` 仍是品牌名/logo 的中心入口
- [ ] `App.tsx` 仍在未登录时显示 `DoneHubLoginGate`
- [ ] `DoneHubLoginGate` 品牌头仍在登录框外部上方

### 6.2 设置页与用户可见裁剪

- [ ] Global tabs 仍只剩 `actorProfiles` / `myProfiles`
- [ ] Group tabs 仍只剩 `guidance` / `automation` / `im` / `blueprint`
- [ ] Guidance 页面仍只显示 Actor Notes
- [ ] IM Bridge 下拉和状态卡片仍显示“飞书/钉钉”
- [ ] IM Bridge 的英文/日文 locale 仍统一使用 `Feishu` 命名，不回退到 `Feishu/Lark` / `Lark (Global)`
- [ ] 账户弹层仍保留“跳转至算力中心 / 刷新余额 / 退出登录”

### 6.3 DoneHub 接入链

- [ ] `src/cccc/ports/web/app.py` 仍注册 done-hub 路由
- [ ] `/api/v1/done_hub/login` 仍负责单次 provisioning
- [ ] `/api/v1/done_hub/self` 仍纯刷新、无副作用
- [ ] `session.group == "pro"` 时仍跳过本地配置写入
- [ ] token 查找仍是分页遍历 + 同名最大 `id` + create 后 re-list
- [ ] `available_model` 仍按首个 `gpt*` / `gemini*` 取模型名
- [ ] `.codex/auth.json`、`.codex/config.toml`、`.gemini/.env`、`.gemini/settings.json` 写入格式仍严格一致
- [ ] `config.toml` 仍只替换首个 `[projects` 之前的前缀

### 6.4 前端承接与文案边界

- [ ] `useDoneHubStore` 仍负责登录/恢复/刷新/退出
- [ ] `DoneHubSession` 仍带 `group`
- [ ] Header 与 Mobile 仍都显示余额入口
- [ ] `group === "pro"` 时 Header 与 Mobile 仍都显示金色 `PRO`
- [ ] Header、Mobile、Settings、Guidance 的强调色仍是当前 sky/blue token，不回退到旧 emerald token
- [ ] 余额仍统一保留两位小数
- [ ] 错误文案仍不会直出 `.codex/.gemini` 路径、文件名或原始写盘异常

### 6.5 回归验证

- [ ] `tests/test_web_done_hub_routes.py` 仍存在且能通过
- [ ] merge 后至少重跑 done-hub 相关后端测试
- [ ] merge 后至少做一次人工浏览器检查：
  - 登录页
  - 右上角账户入口
  - `PRO` 标识
  - 账户弹层
  - Settings 隐藏项
  - Guidance actor-only
  - IM Bridge 飞书/钉钉展示

## 7. 当前剩余边界

这份文档基于代码对比与已存在测试整理。

当前仍需如实保留的边界只有一条：

- 品牌/UI 收口线尚未形成完整前端自动化测试，后续 merge 完最新 CCCC 后，仍建议做一轮人工浏览器视觉回归。
