# Backup e restauração

## Backup manual
No ambiente com `DATABASE_URL` configurada:

```bash
python backup_postgres.py
```

O backup é `.sql.gz`. Se S3/R2 estiver configurado, também é enviado ao bucket.

## Backup agendado
Agende `./scripts/backup.sh` diariamente no provedor ou cron externo. Para produção, retenção sugerida no arquivo exemplo: 30 dias.

## Restauração
Restaure primeiro em um banco novo/descartável. Nunca teste restauração diretamente no banco ativo.

```bash
set CONFIRM_RESTORE=YES
python restore_postgres.py backups/barbersaas-AAAAmmddTHHMMSSZ.sql.gz
```

No Linux/macOS use `export CONFIRM_RESTORE=YES`.

Depois rode `python migrate.py` e valide `/ready`.
