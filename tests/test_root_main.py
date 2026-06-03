# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib


def test_root_main_delegates_to_app_main(monkeypatch):
    root_main = importlib.import_module("main")
    calls: list[str] = []

    def fake_loader():
        calls.append("load")

        def fake_app_main():
            calls.append("run")

        return fake_app_main

    monkeypatch.setattr(root_main, "_load_app_main", fake_loader)

    root_main.main()

    assert calls == ["load", "run"]
