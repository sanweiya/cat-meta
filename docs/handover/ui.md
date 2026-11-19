# 界面与扩展

[返回接手文档索引](README.md)

## 1. MainWindow 结构

`catmeta/ui/window.py`：

```text
Gtk.ApplicationWindow
├── Gtk.HeaderBar
│   ├── 自定义标题/副标题（当前页面）
│   ├── 代理模式 Gtk.ComboBoxText
│   └── 主菜单按钮
└── SidebarSplit (Gtk.Overlay)
    ├── Sidebar（固定宽度容器）
    └── 内容区
        └── Gtk.Overlay
            ├── Gtk.Stack（7 个页面）
            └── Toast（底部居中提示）
```

- 页面标题实际显示在 HeaderBar，不在页面内；`PageHeader` 仍保存标题/副标题供窗口同步。
- 页面栈使用 `Gtk.Stack`，切页不做过渡动画，保证原生感。
- Toast 用 `Gtk.Revealer` + `.app-toast` 样式，错误/成功使用 `toast-error`/`toast-success`，不要用主题的通用 `.error`/`.success` 类（会继承 Greybird 的消息框配色）。
- 页面顺序和 `Ctrl+1…7` 快捷键共用 `PAGE_ORDER`；新增第 8 页时需同时调整快捷键范围。

## 2. 页面组织

所有页面在 `catmeta/ui/pages/`，并继承 `ui/widgets/common.py::BasePage`：

| 页面 | 文件 | 特点 |
| --- | --- | --- |
| 概览 | `home.py` | 状态卡、统计、Cairo 流量图、快捷开关 |
| 代理 | `proxies.py` | 分组下拉框、搜索、列自定义、自绘操作列、测速 |
| 订阅 | `profiles.py` | 卡片列表、导入/编辑/导出、外部编辑器监听 |
| 连接 | `connections.py` | 原生 TreeView、搜索、详情、关闭连接 |
| 规则 | `rules.py` | 运行时/订阅规则、搜索、复制 |
| 日志 | `logs.py` | WebSocket 日志、级别/关键词过滤、暂停、导出 |
| 设置 | `settings.py` | 分组设置行、内核下载/选择、TUN 授权、Geo 下载 |

`BasePage` 提供：

- `header`：页面工具栏，`header.lead` 放搜索/分组控件，`header.actions` 放按钮；没有 action 的页面会被窗口隐藏整行工具栏。
- `body`：可滚动内容区，常用样式类 `card`、`native-group`、`native-stats`、`data-list`、`page-body`。
- `on_show()`：首次进入/再次显示时懒加载数据。
- `refresh_fonts()`：系统字体/主题变化时刷新自绘 renderer 的字体（表格页需要实现）。

事件分发有两种模式：

1. 由 `MainWindow._subscribe()` 统一接收 `state`/`traffic`/`metrics`/`settings` 等，再显式调用页面 `on_*`（大部分页面）。
2. 页面自己 `controller.subscribe()`（日志页订阅 `logs-cleared`，部分页面按需订阅）。

## 3. 侧栏宽度

`catmeta/ui/sidebar.py::SidebarSplit` 是近期改动重点，结构如下：

```text
SidebarSplit (Gtk.Overlay)
├── root Gtk.Box
│   ├── _FixedWidthBin → Sidebar（固定宽度）
│   └── content（页面栈 overlay）
└── pass-through Gtk.Fixed 覆盖层
    └── 9px 透明 EventBox（拖拽热区）
```

关键行为：

- `_FixedWidthBin` 的 `get_preferred_width` 永远返回设定宽度，因此网速/版本号/徽标文本不会撑大侧栏。
- 宽度保存在 `settings.sidebar_width`，范围 160–420，默认 200；拖拽结束 320ms 防抖保存，退出时 `save_now()` 兜底。
- 描边由 `.sidebar` 自己的 `border-right: 1px` 绘制。
- 拖拽热区是 9px 宽的透明 EventBox，通过 pass-through `Gtk.Fixed` 定位，**骑跨在描边上**（左 5px / 右 4px），描边正好在热区中心。
- 覆盖层使用 `set_overlay_pass_through(..., True)`，所以内容区按钮/表格仍可正常点击；热区自身带窗口，光标只在描边附近变成拖拽样式。
- 热区高度在 `Gtk.Fixed` 的 `size-allocate` 中通过 idle 同步到整高，避免在分配阶段直接改子控件尺寸。
- 双击热区恢复 200px；悬停/拖动只改描边颜色，不绘制色条。

### 侧栏常见坑

- **不要**把热区改回铺满内容区的 overlay 层：会让光标覆盖整个内容区，并吞掉按钮点击（已经踩过）。
- **不要**让热区占用布局宽度：内容区工具栏横线会和描边脱开。
- 不要把侧栏宽度重新交给 `Gtk.Box`/普通 `Gtk.Paned` 的自然宽度；那样网速文本又会撑宽侧栏。
- 修改侧栏宽度相关逻辑后，至少验证：长网速文本、拖拽、双击复位、重启保留、内容区按钮可点击、工具栏横线无缝。

## 4. 主题、字体与自绘控件

- 主题 CSS：`catmeta/assets/css/style.css`。只使用 `@theme_bg_color`、`@theme_base_color`、`@theme_fg_color`、`@theme_selected_bg_color`、`@borders` 等主题变量，不硬编码浅色/深色。
- 字体缩放：`app.py::_scaled_css()` 只重写 `font-size: Npx;`，系统字体/主题变化会重新加载 CSS；新增字号尽量用 px，才能跟随缩放。
- 表格统一使用 `ui/widgets/table.py` 的等宽 renderer（`BaselineCellRendererText`），保证基线和字体一致。
- 流量图是 `ui/widgets/chart.py` 的 `TrafficChart`，颜色取自当前主题前景色，不依赖第三方图表库。
- 设置行、卡片、空状态、分组标题等复用 `ui/widgets/common.py` 中的 `Card`、`SettingsGroup`、`SettingRow`、`EmptyState`、`PageHeader`。
- 应用图标在 `catmeta/assets/icons/`；托盘图标需要复制到用户 hicolor 主题（`paths.install_user_icons()`），因为 Cinnamon 面板不会读取当前进程追加的图标搜索路径。

## 5. 扩展配方

### 新增页面

1. 在 `ui/pages/` 新建 `XxxPage(BasePage)`，构建 UI，按需实现 `on_show()`/`refresh_fonts()`/`on_*`。
2. 在 `window.py` 的 `PAGE_ORDER`、`PAGE_TITLES`、`_create_pages()` 注册。
3. 在 `sidebar.py` 的 `NAV_ITEMS` 增加导航项（page id、标题、图标）。
4. 如需快捷键，调整 `_on_key_press` 中 `Ctrl+1…7` 的范围。
5. 需要状态/事件时，在 `MainWindow` 的对应 `_on_*` 里转发，或让页面自己 `subscribe()`。

### 新增设置项

1. 在 `settings.py::_defaults()` 加默认值（否则加载时会被丢弃）。
2. 在 `ui/pages/settings.py` 加控件，加入 `_collect()`；保存走 `controller.save_settings()`。
3. 需要重启内核的键加入 `Controller.save_settings()` 的 `restart_keys`；否则用 `api.patch_configs()` 热更新。
4. UI 专用项（窗口/侧栏等）可像 `sidebar_width` 一样直接 `settings.update()`，不触发 `settings` 事件。

### 新增 mihomo API 能力

1. `api.py` 加类型化方法，统一处理 4xx/`ApiError`。
2. `Controller` 加带状态检查的包装（未运行/失败抛 `ControllerError`）。
3. UI 用 `run_async()` 调用，成功/失败回调回主线程；不要在事件回调里阻塞。
4. 测速类 API 注意超时和 408/504 容忍。

### 新增托盘菜单项

`tray.py::_build_menu()` 构建菜单，`update()` 同步状态。XApp 走 `set_secondary_menu()`，Gtk.StatusIcon 走 `popup-menu`，两条路径都要验证。托盘不可用时不要依赖它隐藏窗口。

### 改 UI 后的人工检查

- 系统主题明/暗、HiDPI/字体缩放、Cinnamon 与 XFCE 下布局不破。
- 新增控件使用主题颜色；不要出现固定白底/黑字。
- 表格列宽、省略号、排序/过滤、右键菜单在窄窗口下仍可用。
- 侧栏宽度与内容区工具栏横线的衔接不回退。
