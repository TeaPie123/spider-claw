"""
作用：提供 spider claw 命令行入口。
流程：加载统一任务客户端 → 解析命令 → 提交或查询任务。
"""

from channels.cli import main

if __name__ == "__main__":
    main()
