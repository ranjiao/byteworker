# 本地验证

产品运行不新增 Python 依赖。覆盖率工具是开发依赖，与 CI 使用相同版本区间。

```sh
python3 -m venv /tmp/byteworker-test-venv
/tmp/byteworker-test-venv/bin/python -m pip install 'coverage>=7.10,<8'
/tmp/byteworker-test-venv/bin/python -m coverage run -m unittest discover -s tests -p 'test_*.py'
/tmp/byteworker-test-venv/bin/python -m coverage combine
/tmp/byteworker-test-venv/bin/python -m coverage report
```

需要 Git、Node、Bash 和合格的 Python 3.10+。`.coveragerc` 启用分支统计，报告总覆盖率阈值为 75%。
Viewer 测试启动临时本地服务及执行 JavaScript；数据均为临时合成样例。
临时目录路径在 macOS 可由 `/var` 规范化为 `/private/var`，比较真实路径时先 resolve。
