# 本地验证

运行这些检查需要 Git、Node、Bash 和 Python 3.10+。在临时虚拟环境中安装与 CI 同版本区间的覆盖率工具。

```sh
python3 -m venv /tmp/byteworker-test-venv
/tmp/byteworker-test-venv/bin/python -m pip install 'coverage>=7.10,<8'
/tmp/byteworker-test-venv/bin/python -m coverage run -m unittest discover -s tests -p 'test_*.py'
/tmp/byteworker-test-venv/bin/python -m coverage combine
/tmp/byteworker-test-venv/bin/python -m coverage report
```

`.coveragerc` 启用分支统计，总覆盖率门槛为 75%。Viewer 测试用合成数据启动临时本地服务并运行 JavaScript。
macOS 可能将 `/var` 解析成 `/private/var`。比较路径前先解析到真实路径。
