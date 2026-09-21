-- Rejoue la logique de clustering de main.py pour un client_unique_id donné
-- et affiche le critère de détection retenu pour chaque fiche.

SET @cg_id = 'CG_010222';
-- Si cet ID n'existe pas dans votre base (la requête ne renverrait aucune ligne),
-- décommentez la ligne suivante pour choisir automatiquement un cluster existant :
-- SET @cg_id = (SELECT client_unique_id FROM client_unique GROUP BY client_unique_id HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 1);
SET @shared_threshold = 15;

SELECT
    cu.client_unique_id,
    c.client_id,
    cu.user_id           AS user_id_stocke,
    c.user_id            AS user_id_fiche,
    c.authenticator_id,
    c.profil_urssaf_id,
    c.email_address,
    c.phone,
    c.email_count,
    c.phone_count,
    CASE
        WHEN c.auth_clean IS NOT NULL THEN 'AUTHENTICATOR_ID'
        WHEN c.urssaf_clean IS NOT NULL THEN 'PROFIL_URSSAF_ID'
        WHEN c.email_clean IS NOT NULL AND c.email_count <= @shared_threshold THEN 'EMAIL'
        WHEN c.phone_clean IS NOT NULL AND c.phone_count <= @shared_threshold THEN 'PHONE'
        ELSE 'AUCUN_CRITERE (isolé)'
    END AS critere_detection,
    cu.date_creation,
    cu.date_modification
FROM client_unique cu
JOIN (
    -- COUNT(*) OVER (PARTITION BY ...) : équivalent des sous-requêtes corrélées mais en un seul passage (bien plus rapide sur une grosse table)
    SELECT
        n.*,
        COUNT(*) OVER (PARTITION BY n.email_clean) AS email_count,
        COUNT(*) OVER (PARTITION BY n.phone_clean) AS phone_count
    FROM (
        SELECT
            apc.id AS client_id,
            apc.user_id,
            apc.authenticator_id,
            apc.profil_urssaf_id,
            apc.email_address,
            apc.phone,
            apc.is_delete,
            -- clean() : NULL / '' / '0' / 'null' / 'None' -> considéré comme vide
            NULLIF(NULLIF(NULLIF(TRIM(apc.authenticator_id), ''), '0'), 'null') AS auth_clean,
            NULLIF(NULLIF(NULLIF(TRIM(apc.profil_urssaf_id), ''), '0'), 'null') AS urssaf_clean,
            -- normalize_email() : lower + exclusion des dummy/yopmail
            CASE
                WHEN apc.email_address IS NULL OR TRIM(apc.email_address) = '' THEN NULL
                WHEN LOWER(TRIM(apc.email_address)) IN (
                    'test@test.com', 'contact@acces-sap.net', 'null', 'none',
                    'pasdemail@gmail.com', 'noemail@gmail.com'
                ) THEN NULL
                WHEN LOWER(TRIM(apc.email_address)) LIKE '%@yopmail.com' THEN NULL
                ELSE LOWER(TRIM(apc.email_address))
            END AS email_clean,
            -- normalize_phone() : suppression espaces/points/tirets, +33 -> 0, exclusion dummy / longueur < 10
            CASE
                WHEN apc.phone IS NULL OR TRIM(apc.phone) = '' THEN NULL
                WHEN REPLACE(REPLACE(REPLACE(TRIM(apc.phone), ' ', ''), '.', ''), '-', '') IN (
                    '0000000000', '0102030405', '0600000000', '0123456789', '0900000000', '0101010101', '0112345678'
                ) THEN NULL
                WHEN LENGTH(
                    CASE WHEN REPLACE(REPLACE(REPLACE(TRIM(apc.phone), ' ', ''), '.', ''), '-', '') LIKE '+33%'
                         THEN CONCAT('0', SUBSTRING(REPLACE(REPLACE(REPLACE(TRIM(apc.phone), ' ', ''), '.', ''), '-', ''), 4))
                         ELSE REPLACE(REPLACE(REPLACE(TRIM(apc.phone), ' ', ''), '.', ''), '-', '')
                    END
                ) < 10 THEN NULL
                ELSE
                    CASE WHEN REPLACE(REPLACE(REPLACE(TRIM(apc.phone), ' ', ''), '.', ''), '-', '') LIKE '+33%'
                         THEN CONCAT('0', SUBSTRING(REPLACE(REPLACE(REPLACE(TRIM(apc.phone), ' ', ''), '.', ''), '-', ''), 4))
                         ELSE REPLACE(REPLACE(REPLACE(TRIM(apc.phone), ' ', ''), '.', ''), '-', '')
                    END
            END AS phone_clean
        FROM acces_pro_client apc
        WHERE apc.is_delete = 0
    ) n
) c ON c.client_id = cu.client_id
WHERE cu.client_unique_id = @cg_id
ORDER BY cu.client_id;
