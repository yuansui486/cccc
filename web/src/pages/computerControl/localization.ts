const TOOL_TEXT: Record<string, { name: string; description: string }> = {
  App: { name: "启动或切换应用", description: "打开应用、切换窗口，或调整窗口的位置和大小。" },
  PowerShell: { name: "运行系统命令", description: "执行 PowerShell 命令，用于系统管理和自动化操作。" },
  FileSystem: { name: "文件管理", description: "读取、写入、复制、移动、删除、查找文件和文件夹。" },
  Snapshot: { name: "屏幕识别", description: "查看桌面截图、窗口和可操作的界面元素。" },
  Screenshot: { name: "快速截图", description: "快速取得桌面截图和当前窗口摘要。" },
  Click: { name: "鼠标点击", description: "按坐标或界面元素执行单击、双击、右键或悬停。" },
  Type: { name: "输入文字", description: "在指定位置或输入框中输入文字并可自动提交。" },
  Scroll: { name: "滚动页面", description: "在页面或指定区域中横向、纵向滚动。" },
  Move: { name: "移动或拖拽鼠标", description: "移动鼠标到指定位置，或执行拖放操作。" },
  Shortcut: { name: "键盘快捷键", description: "执行复制、粘贴、切换窗口等组合键。" },
  Wait: { name: "等待一段时间", description: "暂停执行，等待应用、页面或动画完成。" },
  WaitFor: { name: "等待界面状态", description: "等待文字、窗口或界面元素出现、启用或获得焦点。" },
  Scrape: { name: "读取网页内容", description: "获取网页并按指定问题提取所需内容。" },
  MultiSelect: { name: "批量选择", description: "连续选择多个文件、控件或坐标。" },
  MultiEdit: { name: "批量填写", description: "一次向多个输入框填写不同内容。" },
  Clipboard: { name: "剪贴板", description: "读取或设置 Windows 剪贴板文字。" },
  Process: { name: "进程管理", description: "查看正在运行的进程，或结束指定进程。" },
  Notification: { name: "系统通知", description: "发送一条 Windows 桌面通知。" },
  Registry: { name: "注册表", description: "读取、写入、列出或删除 Windows 注册表项。" },
};

const PARAM_TEXT: Record<string, { label: string; description?: string }> = {
  mode: { label: "操作模式", description: "选择该工具要执行的操作。" },
  name: { label: "名称", description: "应用、窗口、进程或注册表值的名称。" },
  window_name: { label: "窗口名称" },
  window_loc: { label: "窗口位置", description: "窗口左上角坐标，例如 100, 100。" },
  window_size: { label: "窗口大小", description: "窗口宽度和高度，例如 1200, 800。" },
  command: { label: "PowerShell 命令" },
  timeout: { label: "超时时间（秒）" },
  duration: { label: "等待时间（秒）" },
  interval: { label: "检查间隔（秒）" },
  path: { label: "文件或目录路径" },
  destination: { label: "目标路径" },
  content: { label: "文件内容" },
  pattern: { label: "匹配规则" },
  recursive: { label: "包含子目录" },
  append: { label: "追加到文件末尾" },
  overwrite: { label: "允许覆盖" },
  offset: { label: "起始行" },
  limit: { label: "数量限制" },
  encoding: { label: "文本编码" },
  show_hidden: { label: "显示隐藏文件" },
  use_vision: { label: "返回截图" },
  use_dom: { label: "识别网页内容" },
  use_annotation: { label: "标注界面元素" },
  use_ui_tree: { label: "识别可操作元素" },
  width_reference_line: { label: "横向参考网格" },
  height_reference_line: { label: "纵向参考网格" },
  display: { label: "显示器编号", description: "留空表示所有显示器。" },
  loc: { label: "屏幕坐标", description: "目标位置，例如 640, 360。" },
  locs: { label: "多个屏幕坐标" },
  label: { label: "界面元素", description: "可填写屏幕识别结果中的元素名称或编号。" },
  labels: { label: "多个界面元素" },
  button: { label: "鼠标按键" },
  clicks: { label: "点击方式" },
  text: { label: "文字内容" },
  clear: { label: "先清空原内容" },
  caret_position: { label: "光标位置" },
  press_enter: { label: "输入后按回车" },
  type: { label: "类型" },
  direction: { label: "滚动方向" },
  wheel_times: { label: "滚动幅度" },
  drag: { label: "执行拖拽" },
  shortcut: { label: "快捷键", description: "例如 Ctrl+C、Alt+Tab。" },
  condition: { label: "等待条件" },
  url: { label: "网页地址" },
  query: { label: "需要提取的内容" },
  use_sampling: { label: "智能整理内容" },
  press_ctrl: { label: "按住 Ctrl 多选" },
  pid: { label: "进程 ID" },
  sort_by: { label: "排序方式" },
  force: { label: "强制执行" },
  title: { label: "通知标题" },
  message: { label: "通知内容" },
  app_id: { label: "发送应用标识" },
  value: { label: "值" },
};

const OPTION_TEXT: Record<string, string> = {
  launch: "启动应用", resize: "调整窗口", switch: "切换窗口",
  read: "读取", write: "写入", copy: "复制", move: "移动或重命名", delete: "删除", list: "列出内容", search: "查找", info: "查看信息",
  left: "左键", right: "右键", middle: "中键", up: "向上", down: "向下", horizontal: "横向", vertical: "纵向",
  start: "开头", end: "末尾", idle: "保持当前位置",
  get: "读取", set: "设置", kill: "结束进程",
  text_exists: "文字出现", active_window: "窗口处于前台", element_exists: "元素出现", element_enabled: "元素可用", focused_element: "元素获得焦点",
  name: "名称", pid: "进程 ID", cpu: "CPU 使用率", memory: "内存占用",
  string: "字符串", dword: "32 位数值", qword: "64 位数值", binary: "二进制", multi_string: "多行字符串", expand_string: "可展开字符串",
  "0": "仅悬停", "1": "单击", "2": "双击",
};

export function toolName(name: string): string {
  return TOOL_TEXT[name]?.name || name;
}

export function toolDescription(name: string, fallback = ""): string {
  return TOOL_TEXT[name]?.description || fallback || "Windows 电脑操作工具。";
}

export function parameterLabel(name: string, fallback = ""): string {
  return PARAM_TEXT[name]?.label || fallback || name.replace(/_/g, " ");
}

export function parameterDescription(name: string, fallback = ""): string {
  return PARAM_TEXT[name]?.description || (/[\u4e00-\u9fff]/.test(fallback) ? fallback : "");
}

export function optionLabel(value: unknown): string {
  const text = String(value);
  return OPTION_TEXT[text] || text;
}
