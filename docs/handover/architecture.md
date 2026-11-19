# 架构与生命周期

[返回接手文档索引](README.md)

## 1. 进程与入口

```text
run.py
└── CatMetaApplication (catmeta/app.py)
    ├── Controller (catmeta/controller.py)
    │   ├── Settings
    │   ├── ProfileStore
    │   ├── CoreManager ── mihomo 子进程
    │   ├── MihomoAPI ──── http://127.0.0.1:<api_port>
    │   ├── WebSocketClient × 2（traffic / logs）
    │   └── SystemProxy
    └── MainWindow (catmeta/ui/window.py)
        ├── SidebarSplit ── Sidebar
        ├── Gtk.Stack ───── 7 个页面
        └── Toast 覆盖层
```

- `run.py` 是唯一入口：初始化日志、调用 `_set_process_name()` 把进程 `comm` 设为 `cat-meta`，再运行 `Gtk.Application`。
- `CatMetaApplication` 使用固定 `APP_ID` 实现单实例；第二次启动会激活已有窗口。
- `Controller` 是状态的唯一编排层；页面不直接操作 `CoreManager`，只通过 Controller 和 Controller 事件通信。
- `CoreManager` 只管内核进程；HTTP/WebSocket 分别由 `api.py` 和 `ws.py` 负责。
- 应用当前只面向 Linux（X11/Wayland，通过 GTK 3），没有 Windows/macOS 分支。

## 2. 启动顺序

1. `run.py` 调用 `setup_logging()`（`CATMETA_DEBUG=1` 时同时输出终端），然后创建 `CatMetaApplication`。
2. `do_startup()`：
   - `ensure_dirs()` 创建 XDG 数据目录。
   - `Controller()`：加载设置、订阅索引，实例化 `CoreManager`/`SystemProxy`；若检测到系统代理当前确实指向本应用端口，会把 `settings.system_proxy` 同步为 true。
   - 启动 1 秒 ticker，订阅 `settings` 事件，注册 GApplication actions。
   - 安装用户图标、加载 CSS（按系统字体缩放重写 `font-size`）、监听主题/字体变化。
   - 注册 SIGTERM/SIGINT，`hold()` 住应用。
3. `do_activate()`：
   - 创建 `MainWindow`（标题栏、侧栏、7 个页面、toast）。
   - 创建托盘（`show_tray` 为 true 时），`show_all()` 并显示窗口；`start_minimized` 且托盘可用时只隐藏窗口。
   - 1 秒后尝试恢复会话：`auto_start_core` 或上次开启了系统代理时自动启动内核。
   - 每 120 秒触发一次自动订阅更新检查；真正是否更新由 `update_interval_hours` 和 `last_profile_update_check` 决定。
4. 页面首次显示走懒加载：`navigate()` 先用 idle 安排 `page.on_show()`，避免切页阻塞首帧。

## 3. 关闭顺序

`quit_app()` / `do_shutdown()`：

1. `window.prepare_quit()`：保存侧栏宽度和窗口几何（外框尺寸 + CSD 内外差值）。
2. `controller.shutdown()`：
   - 设置 `_shutting_down`，停止 ticker 和两个 WebSocket；
   - **始终关闭实际系统代理**，避免代理仍指向已经停止的内核；但保留 `settings.system_proxy` 偏好，供下次启动恢复；
   - 按 `stop_core_on_quit` 决定是否停止内核。
3. 托盘隐藏，GTK 应用退出。

关闭窗口时，只有“允许关闭到托盘 + 托盘可用”才隐藏窗口；否则直接退出。托盘没有宿主时 `TrayIcon.available=False`，不会把窗口藏到找不到的地方。

## 4. Controller 事件总线

`Controller` 用 `subscribe(event, callback)` / `emit(event, *args)` 解耦 UI：

| 事件 | 生产者 | 主要消费者 |
| --- | --- | --- |
| `state` | `_set_state()`、内核退出 | 侧栏、概览/代理/连接/规则/设置页、托盘 |
| `traffic` | WebSocket traffic | 侧栏、概览、托盘（窗口层合并到 4Hz） |
| `metrics` | 定时轮询 / 状态变化 | 概览统计、侧栏连接数 |
| `proxy-node` | 当前节点轮询 | 概览、代理页 |
| `tick` | 1 秒 ticker | 概览 |
| `profiles` | 订阅增删改/更新 | 实现 `on_profiles()` 的页面、侧栏、托盘 |
| `settings` | `save_settings()`、系统代理操作 | 概览、设置页、侧栏、托盘 |
| `mode` | 模式切换 | 标题栏模式下拉框、侧栏、概览 |
| `connections` | API 轮询 | 连接页 |
| `log` | WebSocket logs | 日志页 |
| `logs-cleared` | 清空日志 | 日志页（页面自己订阅） |
| `startup-progress` | 内核启动阶段 | 概览 |
| `window-focus` | 窗口 focus-in | 目前无订阅者，保留给后续功能 |

事件处理约定：

- `Controller.emit()` 会判断线程；非主线程自动转 `GLib.idle_add`，因此监听器可以安全更新 GTK。
- 监听器里不要做网络/磁盘重活；用 `run_async()`。
- 某些高频事件在窗口层合并：`traffic` 最多 4Hz；概览/节点轮询仅在窗口可见时运行，隐藏到托盘后暂停。

## 5. 线程模型

| 工作 | 执行位置 |
| --- | --- |
| GTK 绘制、所有 widget 更新 | 主线程 |
| 内核启动/停止、订阅更新、下载 | `run_async()` 创建的 worker 线程 |
| traffic/logs WebSocket 读写 | `ws.py` 每连接一个 daemon 线程，自动重连（退避 1–15 秒） |
| 内核退出监视 | `catmeta-core-watch` daemon 线程 |
| 当前节点轮询 | `catmeta-current-node` daemon 线程，避免阻塞 tick |
| 订阅自动更新 | `catmeta-autoupdate` daemon 线程 |

原则：UI 回调只做轻量状态更新；阻塞调用一律交给 `run_async()`，成功/失败回调通过 `GLib.idle_add` 回到主线程。

## 6. 内核生命周期

详细配置生成规则见 [配置与数据](configuration.md#3-运行时配置)。这里关注进程与状态：

### 启动

`Controller.start()` → `CoreManager.start()`：

1. 检查 TUN 权限（启用 TUN 时）。
2. 清理上次会话残留、仍使用本应用 `runtime/config.yaml` 的 mihomo。
3. 检查混合端口/API 端口未被占用。
4. 推断并准备 GeoIP/GeoSite（缺失时多源下载）。
5. 生成运行时配置，用 `mihomo -t` 校验。
6. 以独立会话启动子进程，stdout/stderr 写入 `logs/core.log`。
7. 轮询 `/version`，最多等待 100 秒。
8. 设置模式、重置统计、启动 WebSocket、进入 `running`；按需应用系统代理。

内核文件发现顺序：

```text
settings.core_path
→ ~/.local/share/cat-meta/bin/mihomo
→ 仓库 bin/mihomo
→ /usr/local/bin/mihomo
→ /usr/bin/mihomo
→ PATH 中的 mihomo
```

### 停止与异常

- `stop()` 对进程组发 SIGTERM，6 秒后 SIGKILL；`_intentional_stop` 防止主动停止被当成崩溃。
- 内核意外退出：停止 WebSocket、关闭实际系统代理、进入 `error`、发桌面通知，并把 `core.log` 尾部带到界面。
- 启动前清理只匹配“使用本应用 runtime 配置”的进程，不会动其他 mihomo 实例。

### 状态

`stopped` → `starting` → `running` → `stopping` → `stopped`；启动/运行异常进入 `error`。侧栏状态点、概览、托盘都基于这个状态。

## 7. 托盘与单实例

- 托盘优先使用 **XApp.StatusIcon**，通过 `set_secondary_menu()` 使用系统原生右键菜单。
- XApp 不可用时退回 `Gtk.StatusIcon`，并在 600ms 后检查 `is_embedded()`；没有 XEmbed 宿主就禁用托盘并恢复主窗口。
- 托盘不可用时，“关闭到托盘”和“启动时最小化”必须回退为正常显示窗口。
- `Gtk.Application` 使用固定 `APP_ID`；支持 `cat-meta --page=settings`、`--minimized`，以及从文件管理器打开 YAML（`do_open` 导入为本地配置）。

## 8. 系统代理与退出

系统代理的两种后端、备份与恢复语义详见 [配置与数据](configuration.md#5-系统代理)。架构上只需要记住：

- `settings.system_proxy` 是**偏好**，不是当前开关状态。
- 应用退出时必然关闭实际系统代理，避免断网；内核重启时按偏好重新应用。
- 系统代理指向 `127.0.0.1:<mixed_port>`，TUN 开启时也可同时使用 TUN。

## 9. 进程名

`run.py::_set_process_name()` 使用 `prctl(PR_SET_NAME)` 把 `/proc/<pid>/comm` 设为 `cat-meta`，系统监视器和 `ps -o comm` 会显示 `cat-meta`。`ps -o args`/`/proc/<pid>/exe` 仍是 `python3 run.py`/`python3`，这是进程模型决定的，不要把它当作异常。
