# 知乎短文草稿（请人工核对平台规则后发布）

## 7B 模型能看出 PR 的问题，却会把证据位置写错：我怎样改造 CodeGuard

我在做 CodeGuard 的真实 PR 验证时，遇到的不是“模型完全看不懂代码”。本地的 `qwen2.5-coder:7b` 能识别 shell 执行风险和测试回归，但早期自由填写文件、行号、规则编号时，引用并不可靠。一个看似合理的结论，如果证据落在错误的位置，对代码审查仍然有害。

我没有把“换一个更贵、更大的模型”当成唯一答案，而是把证据引用权从 LLM 手里拿走。CodeGuard 先固定 PR 的 BASE 和 HEAD commit，分别运行 Ruff、Bandit，并在 Docker 隔离环境中执行 pytest。只有 BASE 通过、HEAD 失败的同一测试，才被标记为引入的回归。diff hunk、静态扫描结果和测试结果进入确定性的 Evidence Registry，各自获得一个 ID。模型输出结构化 JSON，只选择 `evidence_ids`；最终报告中的文件、行号、规则和测试事实由程序从 Registry 解析。不存在的 ID 会被拒绝；无法可靠映射到源码行的回归保留测试级证据，不猜测位置。

我用公开的 `codeguard-demo` 做了两个受控的真实 PR 验证：干净的 PR #1 得到 0 个 finding；故意引入问题的 PR #2 检出了预期的安全与回归类别，最终 2 个 finding 均完成证据绑定。这只是两个 validation cases，不能当作统计 benchmark，也不能推出对任意仓库或模型都可靠。

项目源码与运行说明：https://github.com/shuhanzhou01-stack/codeguard

真实验证记录：https://github.com/shuhanzhou01-stack/codeguard/blob/main/docs/REAL_WORLD_VALIDATION.md

欢迎从证据绑定、BASE/HEAD 对比或陌生开发者上手体验的角度提出意见。
