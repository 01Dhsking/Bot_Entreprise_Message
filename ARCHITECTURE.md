# Architecture

Le projet reprend les principes d'`Automation-LinkedIn` : un navigateur Chromium partage,
un serveur MCP disponible en `stdio` ou en `SSE`, un endpoint de sante et une image Docker
autonome.

## Modules

- `registry_client.py` : collecte paginee et recherche via les API publiques des deux annuaires.
- `browser.py` : diagnostic Playwright des interfaces web en cas de changement de portail.
- `mcp_server.py` : expose les outils MCP et les transports `stdio`/`SSE`.
- `database.py` : moteur SQLAlchemy asynchrone et controle PostgreSQL.
- `models.py` : entreprises, collectes, instantanes et historique de contact.
- `repository.py` : sauvegarde idempotente et lecture des donnees.
- `outreach.py` : rendu des modeles et adaptateurs SMTP/Evolution API.
- `schemas.py` : structures independantes du navigateur et de PostgreSQL.
- `config.py` : toutes les variables d'environnement validees au demarrage.

## Donnees

`companies` conserve la derniere version connue d'une societe ou d'un etablissement. La cle
source evite les doublons entre collectes. `page_snapshots` permet de ne pas relire une page
deja collectee et `scrape_runs` audite chaque lot.

`contact_attempts` impose une unicite `(company_id, channel)`. Le statut `sent` bloque tout
nouvel envoi sur ce canal. Les statuts `failed` peuvent etre retentes et `do_not_contact`
bloque tous les canaux.

Le serveur ne lance pas de collecte ou campagne automatiquement. Chaque appel est borne,
les pages deja presentes sont sautees, les campagnes demandent un apercu puis une confirmation.
