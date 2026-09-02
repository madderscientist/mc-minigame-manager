from pathlib import Path

from mc_manager.services.server_properties import update_server_properties


def test_writer_prevents_java_line_continuation_into_managed_values(
    tmp_path: Path,
) -> None:
    properties = tmp_path / "server.properties"
    properties.write_text(
        "aaa=untrusted\\\n"
        "unsafe key=value\n"
        "enable-command-block=false\n",
        encoding="utf-8",
    )

    update_server_properties(
        properties,
        {
            "enable-command-block": "true",
            "level-name": "world",
        },
    )

    lines = properties.read_text(encoding="utf-8").splitlines()
    untrusted = next(line for line in lines if line.startswith("aaa="))
    assert len(untrusted) - len(untrusted.rstrip("\\")) == 2
    assert "unsafe key=" not in lines
    assert "enable-command-block=true" in lines
    assert "level-name=world" in lines
