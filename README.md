# req-test-plan

需求级测试工具包：把一条需求拆成可核对的测试计划，并实测这个模块**能不能交付**。

只依赖 Python 3.11+ 标准库，无第三方依赖。

## 装

软链到各 agent 的 skills 目录，改一处全生效：

```bash
ln -s "$(pwd)" ~/.agents/skills/req-test-plan
ln -s ~/.agents/skills/req-test-plan ~/.claude/skills/req-test-plan
ln -s ~/.agents/skills/req-test-plan ~/.codex/skills/req-test-plan
ln -s ~/.agents/skills/req-test-plan ~/.config/opencode/skills/req-test-plan
```

`~/.agents/skills/req-test-plan` 是稳定入口，`~/.agents/testkit/doctor.sh` 从这里跑回归套件。

## 四条命令

```bash
# 1. 生成需求测试计划骨架（扫目标模块的已有 helper + 项目自己的测试车道）
python3 ~/.agents/skills/req-test-plan/scripts/req_plan.py new <feature> \
  --repo <项目根> --module <子路径> [--module <子路径>...]

# 2. 从计划生成测试文件骨架（按仓库自己的测试约定）
python3 ~/.agents/skills/req-test-plan/scripts/req_plan.py scaffold \
  <计划文件> --repo <项目根> [--out-dir <生成根>]

# 3. 核对计划 vs 代码实况（报红退出码 1）
python3 ~/.agents/skills/req-test-plan/scripts/req_plan.py check \
  <计划文件> --repo <项目根>

# 4. 模块交付就绪度判定（一条命令，实测）
python3 ~/.agents/skills/req-test-plan/scripts/readiness.py <模块路径> --repo <项目根> \
  [--plan <需求测试计划>] [--include-itest] [--test-tenant <名字或前缀>] [--json]
```

判据全表与用法细节见 [SKILL.md](SKILL.md)。

## 设计要点

- **门槛归仓库所有**。仓库有覆盖率闸门就驱动它，判定看它的退出码；通用阈值只在仓库没有闸门时兜底。
- **实测优先**。每条判据要么有实测证据，要么明说环境没就绪（缺什么条件），不拿静态推断冒充实测。
- **跑不起来 ≠ 不达标**。setup 报错、缺产物、连不上中间件，都判 skipped 并说清缺什么。
- **判交付看生产租户**。测试租户的漂移是夹具状态，报 warn 不阻塞。
- **跨存储字段级比对**。同一个 id 在权威库与派生库里的同一个字段值对不上，点名 id 与字段。

## 语言支持

| | 计划生成 | 覆盖率实测 | 导出引用判据 | 跨存储对账 |
| --- | --- | --- | --- | --- |
| TypeScript / JavaScript | ✅ | ✅ node / vitest / jest | ✅ | ✅ |
| Python | ✅ | ✅ pytest-cov | ✅ | ⚠️ 只查 PG |
| Rust / Go | 部分（复用侦察） | ❌ | ❌ | ❌ |

Rust / Go 模块上 `readiness.py` 退出码 2 并说明"当前只实现了 TS/JS/Python"，不给假结论。

## 安全须知

`readiness.py` 会**执行目标仓库自己定义的脚本**（package.json 里的 `test`、`coverage`、`reconcile` 等），并把完整环境变量传给子进程。信任一个仓库的 `test` 脚本，就等于把它交给了你的环境变量（含 `DATABASE_URL` 等）。

`req_plan.py scaffold` 会向仓库写测试文件。钉里的路径在写入前不做仓库边界校验，钉里的用例名不做转义。

## 已知缺口

发布前需处理的问题清单见 [docs/oss-release-review.md](docs/oss-release-review.md)，其中 4 条是一票否决级（零测试假绿、测试发现作用域、`scaffold` 路径穿越、`scaffold` 代码注入），另有 5 条文档与实现矛盾。

## 回归套件

```bash
for t in ~/.agents/skills/req-test-plan/scripts/test_*.py; do python3 "$t" || echo "FAIL $t"; done
```

`test_req_plan_r3.py` 与 `test_req_plan_r4.py` 依赖另一个 skill（`ai-testing-system`）的脚本，路径不存在时这两个文件会失败或崩溃。

## License

MIT
