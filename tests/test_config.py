# -*- coding: utf-8 -*-
"""Config 环境变量解析测试（DOUBAN_AUTH / TRAKT_AUTH 多格式）。

Config 在类定义时读取环境变量，因此测试通过 importlib.reload 重建模块；
reload 期间屏蔽 load_dotenv，避免真实 .env 泄漏进断言，
tearDown 再以真实环境 reload 一次恢复原状。
"""
import importlib
import os
import unittest
from unittest import mock


CONFIG_ENV_KEYS = [
    "DOUBAN_AUTH", "DOUBAN_COOKIE", "DOUBAN_USER_ID", "DOUBAN_USERNAME", "DOUBAN_PASSWORD",
    "TRAKT_AUTH", "TRAKT_CLIENT_ID", "TRAKT_ACCESS_TOKEN", "TRAKT_CLIENT_SECRET", "TRAKT_REFRESH_TOKEN",
]


class ConfigParsingTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in CONFIG_ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module("core.config"))

    def _load(self, **env):
        for key in CONFIG_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(env)
        module = importlib.import_module("core.config")
        try:
            import dotenv
        except ImportError:
            dotenv = None
        if dotenv is not None:
            with mock.patch.object(dotenv, "load_dotenv"):
                return importlib.reload(module).Config
        return importlib.reload(module).Config

    # --- DOUBAN_AUTH ---

    def test_douban_auth_cookie_extracts_user_id(self):
        cfg = self._load(DOUBAN_AUTH='dbcl2="123456:abcdef"; ck=XYZ; bid=abc')
        self.assertEqual(cfg.DOUBAN_COOKIE, 'dbcl2="123456:abcdef"; ck=XYZ; bid=abc')
        self.assertEqual(cfg.DOUBAN_USER_ID, "123456")
        self.assertEqual(cfg.DOUBAN_USERNAME, "")

    def test_douban_auth_user_password_format(self):
        cfg = self._load(DOUBAN_AUTH="user@example.com:secret")
        self.assertEqual(cfg.DOUBAN_USERNAME, "user@example.com")
        self.assertEqual(cfg.DOUBAN_PASSWORD, "secret")
        self.assertEqual(cfg.DOUBAN_COOKIE, "")

    def test_douban_cookie_fallback_extracts_user_id(self):
        cfg = self._load(DOUBAN_COOKIE='dbcl2="654321:zzz"')
        self.assertEqual(cfg.DOUBAN_USER_ID, "654321")

    # --- TRAKT_AUTH ---

    def test_trakt_auth_hash_format(self):
        cfg = self._load(TRAKT_AUTH="cid#tok")
        self.assertEqual(cfg.TRAKT_CLIENT_ID, "cid")
        self.assertEqual(cfg.TRAKT_ACCESS_TOKEN, "tok")
        self.assertEqual(cfg.TRAKT_REFRESH_TOKEN, "")

    def test_trakt_auth_hash_full_format(self):
        cfg = self._load(TRAKT_AUTH="cid#tok#ref#sec")
        self.assertEqual(
            (cfg.TRAKT_CLIENT_ID, cfg.TRAKT_ACCESS_TOKEN, cfg.TRAKT_REFRESH_TOKEN, cfg.TRAKT_CLIENT_SECRET),
            ("cid", "tok", "ref", "sec"),
        )

    def test_trakt_auth_pure_token_uses_default_client_id(self):
        cfg = self._load(TRAKT_AUTH="puretoken")
        self.assertEqual(cfg.TRAKT_ACCESS_TOKEN, "puretoken")
        self.assertEqual(cfg.TRAKT_CLIENT_ID, cfg.DEFAULT_TRAKT_CLIENT_ID)

    def test_trakt_auth_bearer_prefix(self):
        cfg = self._load(TRAKT_AUTH="Bearer abc123")
        self.assertEqual(cfg.TRAKT_ACCESS_TOKEN, "abc123")
        self.assertEqual(cfg.TRAKT_CLIENT_ID, cfg.DEFAULT_TRAKT_CLIENT_ID)

    def test_trakt_auth_console_paste(self):
        cfg = self._load(TRAKT_AUTH="TRAKT_CLIENT_ID=abc123 TRAKT_ACCESS_TOKEN=xyz789")
        self.assertEqual(cfg.TRAKT_CLIENT_ID, "abc123")
        self.assertEqual(cfg.TRAKT_ACCESS_TOKEN, "xyz789")

    def test_trakt_auth_json_format(self):
        cfg = self._load(TRAKT_AUTH='{"client_id": "jcid", "access_token": "jtok", "refresh_token": "jref"}')
        self.assertEqual(cfg.TRAKT_CLIENT_ID, "jcid")
        self.assertEqual(cfg.TRAKT_ACCESS_TOKEN, "jtok")
        self.assertEqual(cfg.TRAKT_REFRESH_TOKEN, "jref")

    def test_trakt_auth_client_secret_pair(self):
        cfg = self._load(TRAKT_AUTH="myid:mysecret")
        self.assertEqual(cfg.TRAKT_CLIENT_ID, "myid")
        self.assertEqual(cfg.TRAKT_CLIENT_SECRET, "mysecret")

    def test_validation_helpers(self):
        cfg = self._load(TRAKT_AUTH="puretoken", DOUBAN_AUTH='dbcl2="1:x"; ck=y')
        self.assertTrue(cfg.validate_trakt())
        self.assertTrue(cfg.validate_douban())
        cfg_none = self._load()
        self.assertFalse(cfg_none.validate_trakt())
        self.assertFalse(cfg_none.validate_douban())


if __name__ == "__main__":
    unittest.main()
