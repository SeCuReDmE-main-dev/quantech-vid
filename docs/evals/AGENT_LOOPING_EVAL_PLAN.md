# QuaNTecH‑ViD — plan d’évaluation des boucles d’agent

## Portée

Ce plan prépare l’intégration WebMCP et OpenAI Agents SDK autour du moteur QuaNTecH‑ViD. Le dépôt actuel expose un studio FastAPI local, des jobs SQLite et un pipeline de rendu FFmpeg direct. Les outils WebMCP, le side panel complet et l’orchestration Agents SDK représentent la prochaine couche; ce document évite de les présenter comme déjà livrés.

## Contrat proposé

Chaque appel d’outil produit :

- `trace_id` et `turn_id`;
- `goal_id`;
- `tool_name` et arguments normalisés;
- `state_before` et `state_after`;
- `evidence_ids`;
- `artifact_hashes`;
- `approval_state`;
- `progress`;
- `disposition` parmi `READY`, `NEEDS_INPUT`, `NEEDS_APPROVAL`, `RUNNING`, `COMPLETE`, `FAILED_TYPED`, `AMBIGUOUS`, `LOOP_WARNING` et `LOOP_BLOCKED`.

Empreinte :

```text
hash(goal_id, source_version, project_version, tool_name,
     normalized_arguments, state_after, approval_state)
```

## Règles de boucle

1. Deux observations successives de la même empreinte avec progrès nul déclenchent une récupération structurée unique.
2. La récupération choisit une action qui change l’état, ajoute une preuve ou réduit précisément l’incertitude.
3. Une nouvelle répétition arrête le run avec `LOOP_BLOCKED` et un reçu lisible.
4. `max_turns` fournit un plafond secondaire.
5. Une action de rendu `AMBIGUOUS` appelle d’abord `GET /api/v1/renders/{id}`.
6. Une reprise conserve la même clé d’idempotence et attend une preuve que le rendu initial reste incomplet.

## Correspondance avec les surfaces actuelles

| Besoin agentique | Surface actuelle | Outil WebMCP cible |
|---|---|---|
| inspecter un projet | validation et manifeste de projet | `inspect_source` |
| valider avant mutation | endpoint de validation | `validate_project` |
| lancer un rendu | `POST /api/v1/renders` | `request_render` |
| relire un résultat incertain | `GET /api/v1/renders/{id}` | `get_render_status` |
| interrompre | `POST /api/v1/renders/{id}/cancel` | `cancel_render` |
| lister les preuves | endpoint d’artefacts | `list_artifacts` |

## Suite d’evals

| ID | Fixture | Assertion principale |
|---|---|---|
| E01 | document valide | storyboard produit en un parcours borné |
| E02 | source manquante | arrêt `NEEDS_INPUT` |
| E03 | inspection identique répétée | boucle détectée |
| E04 | storyboard inchangé | hash stable classé progrès nul |
| E05 | révision A↔B | cycle de longueur deux détecté |
| E06 | avertissement persistant | une récupération, puis `LOOP_BLOCKED` |
| E07 | rendu avant approbation | zéro création de job |
| E08 | timeout après création | statut relu, même clé, zéro double rendu |
| E09 | récupération hors sujet | grader de récupération en échec |
| E10 | texte fidèle, média divergent | grader visuel ou audio en échec |

## Données de trace à conserver

- noms et versions des workflows;
- appels de modèles et outils;
- handoffs et approbations;
- guardrails déclenchés;
- temps par étape;
- empreintes d’état et de storyboard;
- identifiants de jobs et clés d’idempotence;
- scores de récupération, de grounding visuel et de grounding audio;
- classe finale et raison de l’arrêt.

Les contenus sensibles restent exclus des traces exportées. Les identifiants et hashes permettent l’audit sans copier les documents complets.

## Tableau de bord minimal

- taux de complétion;
- tours moyens jusqu’à l’achèvement;
- taux d’états répétés;
- appels dupliqués;
- récupération réussie;
- arrêts prématurés;
- contournements d’approbation, cible `0`;
- doubles rendus par clé, cible `0`;
- scores de récupération;
- scores visuels et audio.

## Ordre d’implémentation

1. Sceller les schémas d’état et de reçu.
2. Ajouter les identifiants de corrélation et clés d’idempotence aux jobs.
3. Envelopper les API existantes par des outils WebMCP bornés.
4. Ajouter l’agent et le `Runner` avec tracing et plafond de tours.
5. Implémenter le détecteur d’empreintes et la récupération unique.
6. Construire les dix fixtures et graders.
7. Afficher trace, approbation et raison d’arrêt dans le side panel.
8. Rejouer la suite après chaque correction issue d’une trace.

## Références

- [OpenAI Agents SDK — Running agents](https://openai.github.io/openai-agents-python/running_agents/)
- [OpenAI Agents SDK — Tracing](https://openai.github.io/openai-agents-python/tracing/)
- [OpenAI — Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)
