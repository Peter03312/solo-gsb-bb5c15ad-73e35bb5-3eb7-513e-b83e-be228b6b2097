"""命令行入口：创建/升级数据库表结构（幂等）。"""
from .database import init_db

if __name__ == "__main__":
    init_db()
    print("database initialized")
