import logging
import os
import socket
import subprocess
import time
from collections import Counter

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Filtres pour écarter les valeurs génériques/bidons
DUMMY_PHONES = {
    "0000000000",
    "0102030405",
    "0600000000",
    "0123456789",
    "0900000000",
    "0101010101",
    "0112345678",
}

DUMMY_EMAILS = {
    "test@test.com",
    "contact@acces-sap.net",
    "null",
    "none",
    "pasdemail@gmail.com",
    "noemail@gmail.com",
}

# Seuil au-delà duquel un email/téléphone est considéré partagé (agence) et ignoré du clustering
SHARED_THRESHOLD = int(os.getenv("SHARED_THRESHOLD", "15"))
# Taille des lots pour les requêtes d'insertion/mise à jour
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10000"))


def is_port_open(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex((host, int(port))) == 0
    except Exception:
        return False


def get_ssh_tunnel_config():
    """Charge la configuration du tunnel SSH depuis l'environnement (aucune valeur par défaut sensible)."""
    host = os.getenv("SSH_BASTION_HOST")
    user = os.getenv("SSH_BASTION_USER")
    remote_target = os.getenv("SSH_REMOTE_TARGET")

    missing = [
        name
        for name, value in (
            ("SSH_BASTION_HOST", host),
            ("SSH_BASTION_USER", user),
            ("SSH_REMOTE_TARGET", remote_target),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Variables d'environnement manquantes pour le tunnel SSH : " + ", ".join(missing)
        )

    return {
        "host": host,
        "port": os.getenv("SSH_BASTION_PORT", "22"),
        "user": user,
        "remote_target": remote_target,
        "local_port": int(os.getenv("DB_PORT", "3307")),
    }


def start_ssh_tunnel():
    """Démarre le tunnel SSH seulement si le port local n'est pas encore accessible."""
    config = get_ssh_tunnel_config()

    if is_port_open("127.0.0.1", config["local_port"]):
        logger.info("Tunnel SSH déjà actif sur le port %s.", config["local_port"])
        return

    logger.info("Ouverture du tunnel SSH vers %s...", config["host"])
    ssh_cmd = [
        "ssh",
        "-L",
        f"{config['local_port']}:{config['remote_target']}",
        f"{config['user']}@{config['host']}",
        "-p",
        str(config["port"]),
        "-N",
        "-f",
    ]

    try:
        subprocess.Popen(ssh_cmd)
        logger.info("Tunnel SSH lancé en arrière-plan.")
        time.sleep(2)
    except Exception as exc:
        logger.warning("Avertissement lors du lancement du tunnel SSH : %s", exc)


def get_db_config():
    host = os.getenv("DB_HOST", "127.0.0.1")
    password = os.getenv("DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "La variable d'environnement DB_PASSWORD est obligatoire et n'a pas été définie."
        )
    return {
        "host": host,
        "port": int(os.getenv("DB_PORT", "3307")),
        "user": os.getenv("DB_USER", "accessap_app"),
        "password": password,
        "database": os.getenv("DB_NAME", "accessap_preprod"),
        "autocommit": False,
        "connection_timeout": 10,
    }


def create_connection():
    config = get_db_config()
    try:
        return mysql.connector.connect(**config)
    except mysql.connector.Error as exc:
        logger.error(
            "Erreur de connexion MySQL : host=%s port=%s database=%s - %s",
            config["host"], config["port"], config["database"], exc,
        )
        raise


def clean(value):
    if value is None:
        return None
    val_str = str(value).strip()
    if val_str in ("", "0", "null", "None", "NULL", "NONE"):
        return None
    return val_str


def normalize_email(email):
    email = clean(email)
    if not email:
        return None
    email_lower = email.lower()
    if email_lower in DUMMY_EMAILS or email_lower.endswith("@yopmail.com"):
        return None
    return email_lower


def normalize_phone(phone):
    phone = clean(phone)
    if not phone:
        return None

    phone = phone.replace(" ", "").replace(".", "").replace("-", "")
    if phone.startswith("+33"):
        phone = "0" + phone[3:]

    if phone in DUMMY_PHONES or len(phone) < 10:
        return None

    return phone


def ensure_output_table(cursor):
    cursor.execute("SHOW TABLES LIKE 'client_unique'")
    if cursor.fetchone() is None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS client_unique (
                id INT AUTO_INCREMENT PRIMARY KEY,
                client_unique_id VARCHAR(32) NOT NULL,
                client_id INT NOT NULL UNIQUE,
                user_id INT NULL,
                date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
                date_modification DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_client_unique_id (client_unique_id),
                INDEX idx_user_id (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, item):
        if item not in self.parent:
            self.parent[item] = item
            return item
        if self.parent[item] == item:
            return item
        self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, item1, item2):
        root1 = self.find(item1)
        root2 = self.find(item2)
        if root1 != root2:
            self.parent[root1] = root2


def parse_unique_num(cg_str):
    if not cg_str or not cg_str.startswith("CG_"):
        return 0
    try:
        return int(cg_str.replace("CG_", ""))
    except ValueError:
        return 0


def format_unique_id(num):
    return f"CG_{num:06d}"


def load_existing_mapping(cursor):
    """Charge les correspondances client_id -> client_unique_id déjà enregistrées."""
    cursor.execute("SELECT client_id, client_unique_id FROM client_unique")
    mapping = {row["client_id"]: row["client_unique_id"] for row in cursor.fetchall()}
    max_cg_num = max((parse_unique_num(cg_id) for cg_id in mapping.values()), default=0)
    return mapping, max_cg_num


def fetch_clients(cursor):
    """Récupère les fiches clients actives ayant au moins un email ou un téléphone."""
    cursor.execute(
        """
        SELECT
            id AS client_id,
            user_id,
            authenticator_id,
            profil_urssaf_id,
            email_address,
            phone
        FROM acces_pro_client
        WHERE is_delete = 0
          AND (
              (email_address IS NOT NULL AND TRIM(email_address) != '')
              OR
              (phone IS NOT NULL AND TRIM(phone) != '')
          )
        """
    )
    return cursor.fetchall()


def compute_frequency_counts(clients):
    """Compte les occurrences de téléphones/emails normalisés pour détecter les valeurs partagées (agences)."""
    phone_counts = Counter()
    email_counts = Counter()
    for c in clients:
        p = normalize_phone(c["phone"])
        e = normalize_email(c["email_address"])
        if p:
            phone_counts[p] += 1
        if e:
            email_counts[e] += 1
    return phone_counts, email_counts


def build_clusters(clients, phone_counts, email_counts):
    """Regroupe les fiches en clusters selon la priorité stricte auth > urssaf > email > phone."""
    uf = UnionFind()
    client_data = {}

    for c in clients:
        cid = c["client_id"]
        raw_user_id = clean(c["user_id"])
        auth = clean(c["authenticator_id"])
        urssaf = clean(c["profil_urssaf_id"])
        email = normalize_email(c["email_address"])
        phone = normalize_phone(c["phone"])

        # user_id : uniquement informatif, ne participe pas au clustering
        effective_user_str = raw_user_id or auth
        user_id = int(effective_user_str) if effective_user_str and effective_user_str.isdigit() else None

        client_data[cid] = {"client_id": cid, "user_id": user_id}

        fiche_node = ("CLIENT", cid)
        uf.find(fiche_node)

        # 1. authenticator_id en priorité (user_id exclu du clustering)
        if auth:
            uf.union(fiche_node, ("AUTH", auth))
        # 2. sinon profil_urssaf_id
        elif urssaf:
            uf.union(fiche_node, ("URSSAF", urssaf))
        # 3. sinon email_address
        elif email and email_counts[email] <= SHARED_THRESHOLD:
            uf.union(fiche_node, ("EMAIL", email))
        # 4. sinon phone
        elif phone and phone_counts[phone] <= SHARED_THRESHOLD:
            uf.union(fiche_node, ("PHONE", phone))

    clusters = {}
    for cid in client_data:
        root = uf.find(("CLIENT", cid))
        clusters.setdefault(root, []).append(cid)

    return clusters, client_data


def assign_unique_ids(clusters, client_data, existing_mapping, max_cg_num):
    """Attribue un client_unique_id stable à chaque cluster (réutilise l'existant si déjà connu)."""
    records = []
    next_cg_num = max_cg_num

    for fiches in clusters.values():
        known_cg_ids = sorted(existing_mapping[f] for f in fiches if f in existing_mapping)

        if known_cg_ids:
            assigned_cg = known_cg_ids[0]
        else:
            next_cg_num += 1
            assigned_cg = format_unique_id(next_cg_num)

        for fid in fiches:
            records.append((assigned_cg, fid, client_data[fid]["user_id"]))

    return records


def save_records(cursor, conn, records):
    """Insère/met à jour les enregistrements par lots de requêtes multi-valeurs."""
    total_records = len(records)

    for i in range(0, total_records, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        placeholders = ", ".join(["(%s, %s, %s)"] * len(batch))
        upsert_query = f"""
            INSERT INTO client_unique (client_unique_id, client_id, user_id)
            VALUES {placeholders}
            ON DUPLICATE KEY UPDATE
                client_unique_id = VALUES(client_unique_id),
                user_id = VALUES(user_id)
        """
        flat_params = [item for row in batch for item in row]
        cursor.execute(upsert_query, flat_params)
        conn.commit()

        current = min(i + BATCH_SIZE, total_records)
        pct = (current / total_records) * 100 if total_records else 100.0
        logger.info("Progression : %s/%s fiches enregistrées (%.1f%%)", current, total_records, pct)


def main():
    logger.info("[1/5] Connexion à la base de données MySQL...")
    conn = create_connection()
    read_cursor = conn.cursor(dictionary=True, buffered=True)
    write_cursor = conn.cursor(dictionary=True, buffered=True)

    try:
        ensure_output_table(write_cursor)
        conn.commit()

        logger.info("[2/5] Chargement des clients uniques existants pour garantir la stabilité...")
        existing_mapping, max_cg_num = load_existing_mapping(write_cursor)
        logger.info(
            "-> %s clients déjà enregistrés. Dernier ID utilisé: %s",
            len(existing_mapping), format_unique_id(max_cg_num),
        )

        logger.info("[3/5] Récupération des fiches clients depuis acces_pro_client...")
        clients = fetch_clients(read_cursor)
        logger.info("-> %s fiches clients actives avec contact récupérées.", len(clients))

        phone_counts, email_counts = compute_frequency_counts(clients)

        logger.info("[4/5] Déduplication et calcul des clusters de rapprochement...")
        clusters, client_data = build_clusters(clients, phone_counts, email_counts)
        logger.info("-> %s clients uniques (Golden) identifiés après déduplication.", len(clusters))

        records_to_upsert = assign_unique_ids(clusters, client_data, existing_mapping, max_cg_num)

        logger.info("[5/5] Sauvegarde ultra-rapide dans la table client_unique...")
        save_records(write_cursor, conn, records_to_upsert)

        logger.info("=== TRAITEMENT TERMINÉ AVEC SUCCÈS ===")
        logger.info("Total fiches dans client_unique : %s", len(records_to_upsert))
        logger.info("Nombre de clients uniques (Golden) : %s", len(clusters))

    finally:
        read_cursor.close()
        write_cursor.close()
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error("Le script a échoué : %s", exc)
        raise SystemExit(1)

