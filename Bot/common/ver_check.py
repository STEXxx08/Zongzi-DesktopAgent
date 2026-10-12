def check_pydantic_ver():
    """
    Check whether pydantic version is ok.

    只需要 Pydantic v2：代码使用 v2 的 `model_fields`（v1 没有），
    也不要在 v3 上运行（v3 会移除/改变 v2 的部分行为）。
    """
    import pydantic

    parts = pydantic.version.VERSION.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        raise Exception(f"无法识别的 pydantic 版本：{pydantic.version.VERSION}")

    if major != 2:
        raise Exception(
            f"需要 pydantic 2.x（当前 {pydantic.version.VERSION}）；"
            "请安装 pydantic>=2.5,<3"
        )
    if minor < 5:
        raise Exception(
            f"pydantic {pydantic.version.VERSION} 过旧，请升级到 pydantic>=2.5,<3"
        )
