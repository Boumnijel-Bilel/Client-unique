# Client Unique

Script de déduplication des fiches clients (`acces_pro_client`) permettant de générer un identifiant **client unique (Golden Record)** stable, en regroupant les fiches qui correspondent au même client réel.

## Fonctionnement

Le script `main.py` :

1. Ouvre (si nécessaire) un tunnel SSH vers la base MySQL de préprod.
2. Se connecte à la base de données et crée la table `client_unique` si elle n'existe pas.
3. Charge les correspondances `client_unique_id` déjà attribuées, afin de garantir leur stabilité entre deux exécutions.
4. Récupère toutes les fiches actives (`is_delete = 0`) possédant au moins un email ou un téléphone.
5. Normalise les emails et téléphones (nettoyage, filtrage des valeurs génériques/bidons comme `test@test.com` ou `0000000000`).
6. Regroupe les fiches en clusters via un algorithme **Union-Find**, selon un ordre de priorité strict :
   1. `authenticator_id`
   2. `profil_urssaf_id`
   3. `email_address` normalisé (si non partagé par plus de 15 fiches)
   4. `phone` normalisé (si non partagé par plus de 15 fiches)
7. Attribue à chaque cluster un identifiant `CG_XXXXXX` (réutilise l'existant si déjà connu, sinon incrémente le dernier numéro utilisé).
8. Insère/met à jour les résultats dans la table `client_unique` par lots (`INSERT ... ON DUPLICATE KEY UPDATE`).

Le fichier `check_client_unique.sql` permet de rejouer manuellement la logique de clustering pour un `client_unique_id` donné et de vérifier le critère de détection retenu pour chaque fiche.

## Prérequis

- Python 3.9+
- Accès réseau au bastion SSH de préprod (ou tunnel déjà ouvert sur le port local configuré)
- Un utilisateur MySQL disposant des droits de lecture sur `acces_pro_client` et d'écriture sur `client_unique`

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuration

La configuration se fait via des variables d'environnement, chargées automatiquement depuis un fichier `.env` local (via `python-dotenv`) si présent, sinon depuis l'environnement système :

| Variable | Description | Défaut |
|---|---|---|
| `SSH_BASTION_HOST` | Hôte du bastion SSH | *(à définir obligatoirement)* |
| `SSH_BASTION_PORT` | Port SSH du bastion | `22` |
| `SSH_BASTION_USER` | Utilisateur SSH | *(à définir obligatoirement)* |
| `SSH_REMOTE_TARGET` | Cible distante `host:port` du proxy MySQL | *(à définir obligatoirement)* |
| `DB_HOST` | Hôte MySQL (local, via le tunnel) | `127.0.0.1` |
| `DB_PORT` | Port MySQL local | `3307` |
| `DB_USER` | Utilisateur MySQL | `accessap_app` |
| `DB_PASSWORD` | Mot de passe MySQL | *(à définir obligatoirement)* |
| `DB_NAME` | Nom de la base | `accessap_preprod` |
| `SHARED_THRESHOLD` | Nombre d'occurrences au-delà duquel un email/téléphone est jugé partagé (agence) et ignoré du clustering | `15` |
| `BATCH_SIZE` | Taille des lots pour les insertions/mises à jour en base | `10000` |
| `LOG_LEVEL` | Niveau de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |


> Copiez `.env.example` en `.env` (non versionné, voir `.gitignore`) et complétez les valeurs pour votre environnement.

## Utilisation

Le script suppose que le port local `DB_PORT` (3307 par défaut) est déjà accessible. Ouvrez d'abord le tunnel SSH vers la base de préprod :

```powershell
ssh -L 3307:mysql-tcp-proxy.accessap-preprod-ops.svc.cluster.local:20184 tunnel@bastion.preprod.acces-sap.net -N
```

Laissez cette commande active (ou lancez-la en arrière-plan avec `-f`), puis dans un autre terminal :

```powershell
python main.py
```

## Tests

```powershell
python -m unittest test_main.py
```
