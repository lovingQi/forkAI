# jarvis-fork 项目参考

## 目录

1. 项目边界
2. 关键模块
3. Web 契约
4. 敏感变更
5. 验证与真车边界

## 项目边界

- 仓库：`/home/xbl/Desktop/jarvis-fork`
- 类型：真实叉车单机程序，C++14、CMake、ROS Noetic，包含设备、控制、规划、任务、服务和车载 Web 接口。
- 构建入口：`./jarvis-build.sh`，生成 `bin/jarvis-g`。
- 运行入口：`./launch.sh` 会启动实际程序，可能连接真实设备；没有用户明确批准和已确认安全环境时不得运行。
- ForkAI 通过车端 HTTP `/api/*` 与 WebSocket `/ws/high|low` 集成。

始终先读取仓库根 `AGENTS.md`。不要根据 ForkAI Mock 推断车端行为；以当前车端源码、指定车型配置和实测证据为准。

## 关键模块

| 领域 | 主路径 | 关注点 |
|---|---|---|
| Web 服务 | `src/service/JWebHttpServer.cpp`、`src/service/JWebService.cpp` | 路由、请求体、状态聚合、控制入口 |
| Web 接口声明 | `inc/service/JWebHttpServer.h`、`inc/service/JWebService.h` | 服务边界与方法签名 |
| 车辆模式 | `src/mode/fork`、`inc/mode/fork` | 货叉、充电、自动行驶、跟随和状态机 |
| 动作 | `src/action`、`inc/action` | 物理动作、控制器与完成条件 |
| 设备 | `src/device`、`inc/device` | 驱动、IO、里程计、货叉与执行机构 |
| 任务与调度 | `src/task`、`inc/task`、`ext/bot/src/service/FltTask.cpp` | route、任务生命周期和上报 |
| 安全 | `JActSecure` 相关代码、车辆模式中的安全激活 | 障碍、安全速度和保护链路 |
| 配置 | `params` | 车型、设备、算法、服务与 route 参数 |
| 音频 | `JFltSpeaker`、`LocalAudioPlayer`、`RemoteAudioClient` | 本地/远程播放及车端提示 |
| 构建 | `CMakeLists.txt`、`jarvis-build.sh` | 架构依赖、ROS、第三方库与产物 |

## Web 契约

`JWebHttpServer::Start` 中的路由注册和 `JWebHttpServer::HandleRequest` 中的分发是 HTTP 路径事实来源；`JWebService` 对应方法是请求行为和状态事实来源。

当前源码注册了状态、地图、参数、配置、激光、驱动、停止、空闲、回充、goto、autodrive、person_follow、routes、scheduler、定位、电机、安全、地图、flap、ctrl 和 output 等接口。`/api/control/scheduler` 调用 `JWebService::SchedulerThis`，ForkAI 构造内联路线时必须与该方法当前解析结构一致。

`/ws/high` 与 `/ws/low` 由内嵌 CivetWeb 服务推送不同频率状态。修改字段、频率、状态字符串、route 完成条件或请求体时，必须同步评审 ForkAI 的 `JarvisClient`、`routes_builder`、`FlowEngine`、Mock 和协议测试。

不要只凭接口返回 `succeed` 判断物理任务完成。检查当前 route、mode、子状态和任务物理量，并由车端协议部定义具体车型的完成/失败/中止契约。

## 敏感变更

以下变更至少按 L3 处理：

- 车辆速度、转向、货叉、制动、充电、避障、跟随或任务恢复。
- JMode/JAction 状态转换、完成条件、超时或失败降级。
- 设备驱动、IO、激光、声纳、定位和执行机构参数。
- `JActSecure`、急停、安全距离、限速或其他保护链路。
- 真实车型 `params`、route 或服务配置。
- 会被 ForkAI 语音或任务流直接调用的新控制接口。

系统架构部、车端协议部和功能安全部必须在实施前评审；需要编译以外的仿真、HIL 或真车动作时，先取得用户明确批准。

## 第三方与大文件边界

- `ext/civetweb` 包含大体量第三方源码；除非任务明确针对该依赖，不修改其内部文件。
- `ext/grm/lib` 包含预编译库；不得重写或替换二进制，除非用户明确授权并提供来源。
- `params/map` 可能包含大地图数据；不做无关格式化或批量改写。
- 保持 C++14 和现有代码风格，不以无关重构扩大安全关键差异。

## 验证与真车边界

| 层级 | 最低要求 | 结论限制 |
|---|---|---|
| 静态审查 | 检查调用链、线程、单位、状态、错误和安全保护 | 不能证明可编译或车辆行为 |
| 构建 | 在匹配依赖环境运行 `./jarvis-build.sh` | 只能证明当前环境编译通过 |
| 针对性测试 | 使用已有测试入口、回放或仿真验证受影响逻辑 | 记录测试入口和限制 |
| 契约 | 对照 ForkAI 客户端和 Mock，验证请求/状态结构 | 不能替代物理执行 |
| HIL | 连接受控硬件验证 IO、时序和故障 | 不能代表完整现场工况 |
| 真车 | 低速隔离区逐级验证正常、失败、停止和恢复 | 需要现场人员和用户批准 |

仓库当前没有可作为所有变更统一入口的完整自动化测试套件。不得虚构测试命令或声称自动回归全绿；由质量与发布部根据变更选择构建、已有测试、仿真、HIL 和真车证据。

任何真实车辆验证都要先记录车型、载荷、场地、人员、急停、速度上限、预期动作和停止条件。Agent 不得独立启动 `launch.sh`、调用控制 API 或修改车辆配置。
