# Bot Entreprise Message

Serveur MCP Python pour collecter les annuaires publics du Benin, cibler les entreprises
stockees localement et suivre les prises de contact email ou WhatsApp sans doublon.

## Sources

- `companies` : societes du Guichet InvestBenin.
- `establishments` : etablissements individuels de MonEntreprise.bj.

La collecte utilise les API publiques paginees des deux annuaires. Playwright/Chromium reste
disponible pour diagnostiquer visuellement les portails si leur structure change.

## Capacites MCP

- `inspect_registry` : lire un echantillon de l'une des deux sources.
- `search_registry` : rechercher en ligne dans une source et mettre les resultats en cache.
- `collect_registry_pages` : collecter un lot borne de pages; les pages deja stockees sont ignorees.
- `find_saved_targets` : filtrer sans nouvel appel internet.
- `preview_targeted_messages` : voir exactement les destinataires et messages sans envoyer.
- `send_targeted_messages` : envoyer au maximum 50 messages par appel apres confirmation explicite.
- `list_contact_history` : enumerer les destinataires deja traites avec leurs informations.
- `list_incoming_messages` : lire les reponses WhatsApp recues, non lues par defaut.
- `configure_incoming_webhook` : reconnecter Evolution API a l'inbox persistante.
- `acknowledge_incoming_messages` : marquer les reponses traitees comme lues.
- `set_do_not_contact` : bloquer durablement les futurs envois pour une entreprise.
- `list_scrape_runs` : auditer les collectes.
- `health_check` et `close_browser`.

Les filtres locaux disponibles sont la commune, le quartier, l'activite, la date de creation,
la source, la presence d'un email/telephone et le statut contacte/non contacte.

## Protection anti-doublon

PostgreSQL impose une seule ligne de contact par entreprise et par canal, ainsi qu'une seule
utilisation d'une meme adresse ou d'un meme numero. Un message deja envoye ne peut donc pas
etre renvoye par erreur, meme si deux appels MCP sont lances en meme temps.
Un echec fournisseur peut etre retente, tandis qu'un statut `sent` bloque les nouveaux envois.

## Demarrage Docker

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

En local, le Compose n'expose volontairement aucun port. En production Dokploy, definir
`ENTERPRISE_MESSAGE_HOST` avec le domaine public choisi. Traefik publie alors le serveur MCP sur
`https://<domaine>/mcp` et le controle de sante sur `https://<domaine>/health`. L'ancien endpoint
SSE `/sse` reste disponible pour les clients historiques.

`ENTERPRISE_MESSAGE_HOST` doit contenir uniquement le nom d'hote, sans `https://`, chemin ni slash
final. Exemple : `bot-entreprise.example.com`.

Definir aussi une longue valeur aleatoire `MCP_API_KEY` dans Dokploy. Le endpoint `/mcp` exige
l'en-tete `Authorization: Bearer <MCP_API_KEY>`. Ne jamais placer cette cle dans le depot Git.

Le serveur MCP rejoint `dokploy-network`, tandis que PostgreSQL reste uniquement sur le reseau
prive `enterprise-internal`. Les migrations Alembic sont appliquees automatiquement au demarrage.
La base utilise l'alias prive unique `enterprise-postgres` afin d'eviter les collisions avec les
autres services nommes `postgres` presents sur les reseaux Docker partages.
Le reseau externe Dokploy doit deja exister :

```bash
docker network inspect dokploy-network
```

### Mot de passe PostgreSQL et volume persistant

`POSTGRES_PASSWORD` est obligatoire et doit rester stable. PostgreSQL utilise cette valeur lors de
la toute premiere initialisation du volume. Au demarrage, le conteneur synchronise aussi le mot de
passe du role existant avec la valeur courante avant d'etre declare sain. Cela permet a un ancien
volume d'adopter la configuration Dokploy sans supprimer les donnees.

Le log `PostgreSQL role password synchronized` doit apparaitre avant le demarrage du serveur MCP.
La suppression du volume n'est pas necessaire pour un simple changement de mot de passe.

## Configuration email

L'adresse expediteur est deja definie sur `solvexsolution.org@gmail.com`. Renseigner dans `.env` :

```dotenv
SMTP_USERNAME=solvexsolution.org@gmail.com
SMTP_FROM_EMAIL=solvexsolution.org@gmail.com
SMTP_PASSWORD=mot-de-passe-application-google
```

Le mot de passe du compte Google ne doit pas etre place ici. Utiliser un mot de passe
d'application dedie. Tant que `SMTP_PASSWORD` est vide, les apercus fonctionnent mais aucun
email n'est envoye.

## Configuration WhatsApp

Le connecteur utilise Evolution API. Renseigner uniquement le fichier local `.env` :

```dotenv
WHATSAPP_PROVIDER=evolution_api
EVOLUTION_API_BASE_URL=https://votre-evolution-api.example.com
EVOLUTION_API_KEY=votre-cle
EVOLUTION_API_INSTANCE=Solvexsolution
EVOLUTION_API_DELAY_MS=123
EVOLUTION_API_LINK_PREVIEW=true
EVOLUTION_WEBHOOK_URL=https://bot-entreprise.example.com/webhooks/evolution
EVOLUTION_WEBHOOK_SECRET=une-longue-valeur-aleatoire
```

Les anciens numeros beninois a huit chiffres sont normalises vers le format international
`22901XXXXXXXX` avant l'appel Evolution API. Utiliser HTTPS en production afin que la cle API
et le contenu des messages soient chiffres pendant leur transport.

Les valeurs sensibles ne doivent jamais etre ajoutees a `.env.example`, au Compose ou au depot.
Le fichier `.env` est ignore par Git.

### WAHA et plusieurs numeros

WAHA peut etre utilise comme fournisseur principal tout en conservant l'adaptateur Evolution :

```dotenv
WHATSAPP_PROVIDER=waha
WAHA_API_BASE_URL=https://waha.example.com
WAHA_API_KEY=cle-api-waha
WAHA_DEFAULT_SESSION=default
WAHA_SESSIONS=default,commercial-2
WAHA_WEBHOOK_URL=https://bot-entreprise.example.com/webhooks/waha
WAHA_WEBHOOK_SECRET=une-longue-valeur-aleatoire
WAHA_POLLING_ENABLED=true
```

Chaque numero WAHA doit avoir sa propre session. Dans le Dashboard WAHA, configurer pour chaque
session un webhook `message` vers `WAHA_WEBHOOK_URL` et l'en-tete personnalise
`X-WAHA-Webhook-Secret` avec la valeur `WAHA_WEBHOOK_SECRET`. Les reponses sont ensuite envoyees
avec le fournisseur et la session du message entrant; un numero ne peut donc pas repondre a la
place d'un autre.

Quand aucune URL publique de webhook n'est disponible, le poller WAHA persistant lit les nouveaux
messages des conversations actives. Il est active par defaut et ignore l'historique anterieur a la
creation ou a la reprise d'une conversation.

Les discussions et leur historique sont persistants. Un message `ai_suggested` peut rester en
brouillon, etre approuve immediatement ou programme. Le worker n'envoie que les elements au statut
`scheduled`. Le mode `automatic` est limite a deux messages; la suite doit passer en mode `ai` ou
`human`. Le mode `paused` bloque les messages planifies de la discussion.

Outils MCP associes : `list_whatsapp_sessions`, `list_whatsapp_conversations`,
`get_whatsapp_conversation`, `plan_whatsapp_message`, `update_planned_whatsapp_message` et
`set_whatsapp_conversation_mode`.

Au demarrage, le service enregistre automatiquement le webhook `MESSAGES_UPSERT` dans Evolution
API. Si `EVOLUTION_WEBHOOK_URL` est vide, l'URL est derivee de `ENTERPRISE_MESSAGE_HOST`. Le secret
du webhook est envoye a Evolution dans l'en-tete `Authorization`; s'il est omis, la cle Evolution
deja configuree est utilisee. Les messages entrants sont dedupliques et conserves dans PostgreSQL
avant lecture MCP.

## Modeles de message

Les champs disponibles sont : `{name}`, `{legal_name}`, `{trade_name}`, `{owner_name}`, `{city}`,
`{district}`, `{activity}` et `{registration_number}`.

Exemple :

```text
Bonjour {owner_name},

Nous accompagnons les entreprises comme {name} a {city} dans leur croissance numerique.
Seriez-vous disponible pour un court echange ?
```

Toujours appeler `preview_targeted_messages` avant `send_targeted_messages`. L'envoi reel exige
`confirm_send=true` et reste limite a 50 destinataires par appel.

## Conversations WhatsApp en deux temps

Une campagne WhatsApp ne transmet plus directement le long message commercial. Pour chaque
nouveau destinataire, elle programme d'abord une introduction qui identifie SolvexSolution et
demande l'autorisation de presenter l'offre. La file PostgreSQL espace globalement les
introductions selon le cycle auditable `30`, `60`, puis `120` secondes, y compris entre deux
campagnes.

Une reponse positive explicite programme le message de campagne conserve dans l'historique. Un
refus n'envoie rien, `STOP` place l'entreprise en liste d'exclusion et une reponse ambigue attend
une revue manuelle. Le worker persistant reprend automatiquement les elements encore en file apres
un redemarrage du service.

## Developpement local

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
python -m enterprise_message_bot.mcp_server
```

Commandes de verification :

```powershell
pytest
ruff check .
docker compose config
```
