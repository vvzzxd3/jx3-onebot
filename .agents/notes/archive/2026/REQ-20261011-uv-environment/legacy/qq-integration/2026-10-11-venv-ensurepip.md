> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/qq-integration/2026-10-11-venv-ensurepip.md`。

# 官方服务 ensurepip 故障排查与完整 uv 迁移

- 所属模块：qq-integration
- 建档日期／最近更新：2026-10-11（Asia/Shanghai）
- 状态：uv迁移与离线验证已完成；原Homebrew版本检测异常未修复，真实部署／联调未执行。
- 需求来源：维护者创建虚拟环境报ensurepip退出码1，随后要求“使用uv进行管理吧”，并选择“完整uv项目管理（推荐）”。
- 关联：[部署指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)、[实施总记录](<../../../REQ-20261010-official-weather/legacy/project/2026-10-10-official-implementation.md>)。

## 1. 开始前事实与排查结果

- 已检查Git：维护者存在暂存、未暂存及未跟踪改动，保留不回滚，不读取敏感配置及私钥。
- 当时失败环境的 `.venv-official/pyvenv.cfg`显示其由Homebrew Python3.14.7创建；当时pip不存在，ensurepip自带pip26.2.1。项目此前验证环境为Python3.12.7。2026-10-11模板／匹配器任务链接校验发现该历史文件当前不存在，因此取消失效超链接；不重建该环境，也不据此推断删除原因。
- 当前工具非交互式shell无 `python` 命令，`python3` 指向Homebrew3.14.7；用户交互式shell的alias／PATH未核实。
- Homebrew解释器可以运行，ssl／zlib可以导入；直接执行 `-m ensurepip --upgrade --default-pip -v` 后，内部pip的truststore解析空macOS版本时抛出 `ValueError: invalid literal for int() with base 10: ''`，外层退出码1。
- `platform.mac_ver()`在该Homebrew进程中返回空版本。`sw_vers -productVersion`返回26.2，架构arm64；系统版本plist也可读且包含26.2。uv管理的Python3.12.11最终实测mac_ver为26.2。具体检测差异未继续追查，不能断言是系统损坏或网络／下载失败。

## 2. 目标、非目标与验收

- 初始目标：获取完整异常，优先不删除环境地修复pip。后续由维护者明确改为完整uv项目管理。
- 最终目标：Python版本固定、直接依赖与锁文件统一管理，独立环境可同步、官方服务离线测试通过、文档说明正确。
- 非目标：不重装全局Python，不删除旧环境，不迁移旧插件，不修改业务代码或实际凭证，不启动真实QQ／天气服务，不自动提交。
- 验收：锁文件与元数据一致、新环境依赖可用、60项离线测试与编译检查通过，旧环境保留。

## 3. 历史尝试与决策依据

1. 直接运行ensurepip取得truststore异常，排除只有外层错误时的猜测。
2. 执行 `python3.14 -m venv --without-pip .venv-official`，补齐该未完成环境的脚本，不使用 `--clear`。
3. 用ensurepip本地wheel及单次 `--use-deprecated=legacy-certs` 尝试离线安装。该参数不关闭TLS验证，也未写永久配置；安装仍在packaging计算macOS wheel标签时遇到空版本，退出码2，pip没有安装成功。
4. 维护者要求uv并确认完整迁移，停止上述pip修复方案，保留失败历史，不继续修改Homebrew环境。
5. 本机已有uv0.12.5及uv管理的Python3.12.11，无需下载Python。使用Rust实现的uv同步官方依赖，不依赖失败的ensurepip。

## 4. 实施方案与实际改动

- [项目配置](<../../../../../../../pyproject.toml>)声明五项官方直接依赖，保留此前的版本范围；Python范围改为 `>=3.12,<3.13`；`tool.uv.package=false`维持 `PYTHONPATH=src` 的非打包应用运行方式。旧tool.nonebot配置保留，此次变更不表示旧插件通过验证。
- 新增[Python版本文件](<../../../../../../../.python-version>)固定3.12.11；新增[uv锁文件](<../../../../../../../uv.lock>)，解析14个条目（含虚拟项目本身），实际安装13项依赖。
- 新建 `.venv-official-uv`，通过 `UV_PROJECT_ENVIRONMENT`明确选择；没有覆盖旧 `.venv` 或删除失败环境 `.venv-official`。后者仅保留上述补齐脚本的历史尝试。
- 当时的官方兼容清单由 `uv export` 生成锁定版本；2026-10-16按“后续仅用uv、不用pip安装依赖”的决定停止维护并移除，见[uv专用依赖管理记录](<../../../REQ-20261016-uv-only/legacy/project/2026-10-16-uv-only-dependency-management.md>)。依赖事实始终以项目配置和锁文件为准。
- [忽略规则](<../../../../../../../.gitignore>)取消版本文件忽略并加入新环境忽略；本轮uv缓存设为已忽略的 `.cache/uv`，没有改全局工具配置。
- 同步[README](<../../../../../../../README.md>)、[代理说明](<../../../../../../../AGENTS.md>)、[部署指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)、[文档索引](<../../../../../../../docs/README.md>)与[实施总记录](<../../../REQ-20261010-official-weather/legacy/project/2026-10-10-official-implementation.md>)。
- uv lock／sync访问公共PyPI获取依赖，不属于QQ／天气业务联调。

## 5. 执行步骤

- [x] 检查工作区、解释器来源、环境与标准库。
- [x] 获取完整ensurepip错误并如实记录失败的修复尝试。
- [x] 经维护者确认迁移完整uv管理并保留旧环境。
- [x] 固定Python、生成锁文件、同步隔离环境并导出兼容清单（该清单后于2026-10-16停止维护并移除）。
- [x] 在新环境执行离线验证，同步文档与审查范围。

## 6. 实际验证结果

以下命令在仓库根目录执行，uv相关命令均显式设置 `UV_CACHE_DIR="$PWD/.cache/uv"`；项目命令设置 `UV_PROJECT_ENVIRONMENT=.venv-official-uv`。

| 检查项 | 方法 | 结果／限制 |
| --- | --- | --- |
| 依赖锁定／安装 | `uv lock`、`uv sync --locked` | 成功，Python3.12.11，安装13项依赖 |
| 兼容清单导出 | `uv export --locked --format requirements-txt --no-hashes --no-emit-project --output-file requirements-official.txt` | 成功，锁定直接及传递依赖 |
| 业务离线测试 | `PYTHONPATH=src uv run --locked python -m unittest discover -s tests -v` | 60项通过；使用合成事件、MockTransport、FakeSocket与临时数据库／密钥 |
| 编译检查 | `PYTHONPATH=src uv run --locked python -m compileall -q src/jx3_bot tests` | 通过 |
| 依赖一致性 | `uv pip check --python .venv-official-uv/bin/python` | 13项兼容，通过 |
| 锁文件与离线幂等同步 | `uv lock --check`、`uv sync --locked --offline` | 通过，不需再次下载依赖 |
| 实际解释器 | `uv run --locked --offline python`打印版本、sys.prefix、mac_ver | 3.12.11；新环境路径正确；macOS版本26.2 |
| Git忽略与配置状态 | `git check-ignore`、限定范围 `git status --short` | 新环境／缓存被忽略；版本文件与锁文件可纳入Git |
| 审查 | 限定配置diff／git diff --check、元数据与安装版本核对、文档链接／格式检查 | 通过；6份文档146处本地链接有效，代码围栏／行尾空白检查通过；NoneBot配置保留、锁中无NoneBot／OneBot依赖，安装版本与锁一致；未审查敏感配置内容 |
| 实际平台联调 | 未执行 `jx3_bot run` | 未获取真实QQ Token／连接WSS／调用天气数据API，未创建生产数据库 |

## 7. 使用方式、风险与下一步

```bash
# 每个新终端在仓库根目录先执行，避免uv默认修改旧 .venv
export UV_PROJECT_ENVIRONMENT=.venv-official-uv
uv sync --locked
PYTHONPATH=src uv run --locked python -m unittest discover -s tests -v

# 完成本机安全配置后才执行；此项不会请求供应商API
PYTHONPATH=src uv run --locked python -m jx3_bot check-config
```

- uv默认不安装pip，之后无需再次运行ensurepip。旧Homebrew异常不在本轮继续治理；不声称旧环境已修复。
- 新终端必须显式选择官方独立环境。正式部署依然只支持Linux／macOS；旧插件与其他Python系列未验证。
- 真实凭证配置、城市生产导入、应用权限／网络与授权群试运行，仍按部署指南由维护者执行；本轮离线通过不等于上线。
- 维护者原有暂存／未暂存改动保留；本轮没有自动Git提交。

## 8. 变更历史

| 日期 | 变化 | 依据 |
| --- | --- | --- |
| 2026-10-11 | 初始建档、复现ensurepip错误 | 用户错误与本机检查 |
| 2026-10-11 | 记录离线pip补装失败，改用完整uv管理 | 维护者直接要求及模式确认 |
| 2026-10-11 | 完成uv锁定／隔离环境／导出与60项离线验证，同步文档 | 本机实际命令及结果 |
