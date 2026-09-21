import os
import unittest

import main


class TestDBConfig(unittest.TestCase):
    def test_get_db_config_uses_env_overrides(self):
        os.environ["DB_HOST"] = "db.local"
        os.environ["DB_USER"] = "demo_user"
        os.environ["DB_PASSWORD"] = "demo_pass"
        os.environ["DB_NAME"] = "demo_db"
        os.environ["DB_PORT"] = "3307"

        config = main.get_db_config()

        self.assertEqual(config["host"], "db.local")
        self.assertEqual(config["user"], "demo_user")
        self.assertEqual(config["password"], "demo_pass")
        self.assertEqual(config["database"], "demo_db")
        self.assertEqual(config["port"], 3307)

    def test_get_db_config_falls_back_to_local(self):
        for key in ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_PORT"]:
            os.environ.pop(key, None)
        os.environ["DB_PASSWORD"] = "demo_pass"

        config = main.get_db_config()

        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["database"], "accessap_preprod")
        self.assertEqual(config["port"], 3307)

    def test_get_db_config_requires_password(self):
        for key in ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_PORT"]:
            os.environ.pop(key, None)

        with self.assertRaises(RuntimeError):
            main.get_db_config()


class TestCleanNormalize(unittest.TestCase):
    def test_clean_treats_empty_and_placeholder_values_as_none(self):
        for value in (None, "", " ", "0", "null", "None", "NULL", "NONE"):
            self.assertIsNone(main.clean(value))

    def test_clean_strips_and_keeps_valid_values(self):
        self.assertEqual(main.clean("  abc  "), "abc")
        self.assertEqual(main.clean(42), "42")

    def test_normalize_email_lowercases_and_strips(self):
        self.assertEqual(main.normalize_email("  Foo@Example.COM "), "foo@example.com")

    def test_normalize_email_rejects_dummy_and_yopmail(self):
        self.assertIsNone(main.normalize_email("test@test.com"))
        self.assertIsNone(main.normalize_email("Contact@ACCES-SAP.net"))
        self.assertIsNone(main.normalize_email("someone@yopmail.com"))
        self.assertIsNone(main.normalize_email(None))

    def test_normalize_phone_strips_separators(self):
        self.assertEqual(main.normalize_phone("06 12 34 56 78"), "0612345678")
        self.assertEqual(main.normalize_phone("06.12.34.56.78"), "0612345678")
        self.assertEqual(main.normalize_phone("06-12-34-56-78"), "0612345678")

    def test_normalize_phone_converts_international_prefix(self):
        self.assertEqual(main.normalize_phone("+33612345678"), "0612345678")

    def test_normalize_phone_rejects_dummy_and_too_short(self):
        self.assertIsNone(main.normalize_phone("0000000000"))
        self.assertIsNone(main.normalize_phone("0123456789"))
        self.assertIsNone(main.normalize_phone("06123"))
        self.assertIsNone(main.normalize_phone(None))


class TestUniqueIdHelpers(unittest.TestCase):
    def test_parse_unique_num_extracts_number(self):
        self.assertEqual(main.parse_unique_num("CG_000042"), 42)

    def test_parse_unique_num_returns_zero_for_invalid_input(self):
        self.assertEqual(main.parse_unique_num(None), 0)
        self.assertEqual(main.parse_unique_num(""), 0)
        self.assertEqual(main.parse_unique_num("XX_123"), 0)
        self.assertEqual(main.parse_unique_num("CG_abc"), 0)

    def test_format_unique_id_pads_with_zeros(self):
        self.assertEqual(main.format_unique_id(42), "CG_000042")
        self.assertEqual(main.format_unique_id(0), "CG_000000")


class TestUnionFind(unittest.TestCase):
    def test_union_merges_two_items_under_same_root(self):
        uf = main.UnionFind()
        uf.union("a", "b")
        self.assertEqual(uf.find("a"), uf.find("b"))

    def test_transitive_union_merges_chain(self):
        uf = main.UnionFind()
        uf.union("a", "b")
        uf.union("b", "c")
        self.assertEqual(uf.find("a"), uf.find("c"))

    def test_unrelated_items_have_different_roots(self):
        uf = main.UnionFind()
        uf.union("a", "b")
        uf.find("c")
        self.assertNotEqual(uf.find("a"), uf.find("c"))


class TestBuildClusters(unittest.TestCase):
    def _client(self, client_id, user_id=None, authenticator_id=None, profil_urssaf_id=None,
                email_address=None, phone=None):
        return {
            "client_id": client_id,
            "user_id": user_id,
            "authenticator_id": authenticator_id,
            "profil_urssaf_id": profil_urssaf_id,
            "email_address": email_address,
            "phone": phone,
        }

    def test_clients_sharing_authenticator_id_are_clustered(self):
        clients = [
            self._client(1, authenticator_id="AUTH1"),
            self._client(2, authenticator_id="AUTH1"),
            self._client(3, authenticator_id="AUTH2"),
        ]
        phone_counts, email_counts = main.compute_frequency_counts(clients)
        clusters, _ = main.build_clusters(clients, phone_counts, email_counts)

        self.assertEqual(len(clusters), 2)

    def test_clients_sharing_email_are_clustered(self):
        clients = [
            self._client(1, email_address="foo@bar.com"),
            self._client(2, email_address="foo@bar.com"),
        ]
        phone_counts, email_counts = main.compute_frequency_counts(clients)
        clusters, _ = main.build_clusters(clients, phone_counts, email_counts)

        self.assertEqual(len(clusters), 1)

    def test_shared_phone_above_threshold_does_not_cluster(self):
        clients = [self._client(i, phone="0612345678") for i in range(main.SHARED_THRESHOLD + 5)]
        phone_counts, email_counts = main.compute_frequency_counts(clients)
        clusters, _ = main.build_clusters(clients, phone_counts, email_counts)

        # Le téléphone est partagé par trop de fiches -> chaque fiche reste isolée
        self.assertEqual(len(clusters), len(clients))


if __name__ == "__main__":
    unittest.main()
