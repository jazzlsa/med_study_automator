import os
import re
from pathlib import Path
from typing import List, Union, Optional, Any
from core.multimodal_processor import multimodal_processor
from core.notebooklm_client import notebooklm_client
from core.sheets_client import sheets_client
from core.anki_flashcards import build_flashcards_apkg
from core import anki_connect
from core.drive_sync import drive_sync
from database.db import db_manager
from utils.logger import logger

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_filename(name: str) -> str:
    """Remove caracteres inválidos em nome de arquivo do Windows."""
    return _INVALID_FILENAME_CHARS.sub("_", name).strip()


class Orchestrator:
    """Orquestra o pipeline focado no NotebookLM, transcrições e Sheets."""

    def __init__(self):
        pass

    def process_lesson(
        self,
        unit_code: str,
        lesson_name: str,
        slide_paths: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        audio_paths: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        slide_path: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        audio_path: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        force_reprocess: Optional[bool] = None,
        **kwargs: Any
    ) -> bool:
        """Processa a aula, cria o NotebookLM, gera o Estúdio e registra na planilha.

        O pipeline continua tentando as próximas etapas mesmo se uma etapa crítica
        falhar (o app não deve travar), mas o retorno, o log final e o status salvo
        no banco refletem honestamente o que deu certo e o que não deu - nunca mais
        um "sucesso" falso quando uma etapa crítica (fontes do NotebookLM ou Gemini)
        falhou de verdade.
        """
        step_failures: List[str] = []
        notebook_id = None
        try:
            logger.info(f"Iniciando processamento da aula: [{unit_code}] {lesson_name}")

            raw_slides = slide_paths if slide_paths is not None else slide_path
            raw_audios = audio_paths if audio_paths is not None else audio_path

            slides = [Path(raw_slides)] if isinstance(raw_slides, (str, Path)) else [Path(p) for p in raw_slides if p] if isinstance(raw_slides, list) else []
            audios = [Path(raw_audios)] if isinstance(raw_audios, (str, Path)) else [Path(p) for p in raw_audios if p] if isinstance(raw_audios, list) else []

            # 1. Cria o NotebookLM para a aula - ou reaproveita o de uma tentativa
            # anterior (se ainda existir), pra não acumular notebooks órfãos toda
            # vez que uma aula falhar parcialmente e for retentada automaticamente.
            notebook_title = f"{unit_code} - {lesson_name}"
            previous = db_manager.get_lesson_status(unit_code, lesson_name)
            previous_notebook_id = previous.get("notebook_id") if previous else None

            existing_source_titles: set = set()
            existing_artifact_types: set = set()

            if previous_notebook_id and previous_notebook_id != "N/A":
                if notebooklm_client.notebook_exists(previous_notebook_id):
                    logger.info(
                        f"Reaproveitando o NotebookLM de uma tentativa anterior: {previous_notebook_id}"
                    )
                    create_result = {"success": True, "notebook_id": previous_notebook_id, "error": None}
                    existing_source_titles = notebooklm_client.list_ready_source_titles(previous_notebook_id)
                    existing_artifact_types = notebooklm_client.list_existing_artifact_types(previous_notebook_id)
                    logger.info(
                        f"Fontes já prontas no notebook reaproveitado: {sorted(existing_source_titles) or '(nenhuma)'} | "
                        f"Artefatos já existentes: {sorted(existing_artifact_types) or '(nenhum)'}"
                    )
                else:
                    logger.warning(
                        f"NotebookLM de uma tentativa anterior ({previous_notebook_id}) não foi encontrado "
                        f"(pode ter sido apagado manualmente) - criando um novo."
                    )
                    create_result = notebooklm_client.create_notebook(notebook_title)
            else:
                logger.info(f"Criando NotebookLM para: {notebook_title}")
                create_result = notebooklm_client.create_notebook(notebook_title)

            notebook_id = create_result["notebook_id"]

            # URL de fallback (caso o compartilhamento público falhe) - domínio
            # correto confirmado na prática (notebook.google.com, SEM "lm" -
            # "notebooklm.google.com" dava "Notebook não encontrado" pra quem não
            # era dono, bug real visto em produção).
            notebook_url = f"https://notebook.google.com/notebook/{notebook_id}" if notebook_id else None

            if not create_result["success"]:
                logger.error(f"Falha ao criar o NotebookLM '{notebook_title}'; pulando fontes e Estúdio.")
                step_failures.append(f"criação do NotebookLM ({create_result['error']})")
            else:
                # Notebook nasce privado por padrão - sem isso a turma não consegue
                # abrir o link da planilha sem pedir acesso manualmente. Idempotente
                # (chamar de novo num notebook reaproveitado que já está público não
                # tem efeito colateral), por isso roda sempre que create_result deu certo.
                # Usa o share_url retornado (link de verdade que funciona pra quem não
                # é dono) em vez do fallback montado à mão, quando disponível.
                sharing_result = notebooklm_client.set_public_sharing(notebook_id)
                if sharing_result.get("share_url"):
                    notebook_url = sharing_result["share_url"]

            # 2. Extrai transcrição e resumo via Gemini ANTES de adicionar as fontes ao
            # NotebookLM, pra poder incluir a transcrição gerada como uma fonte extra
            # (junto com o slide e o áudio originais, não no lugar deles). Roda mesmo
            # se a criação do notebook tiver falhado - a transcrição/resumo local ainda
            # tem valor sozinha, e não deve ficar refém do NotebookLM estar de pé.
            logger.info("Extraindo resumos e transcrição via Gemini...")
            gemini_result = multimodal_processor.analyze_lesson_materials(
                slide_paths=slides,
                audio_paths=audios,
                lesson_name=lesson_name,
                unit_code=unit_code
            )
            if not gemini_result.get("success"):
                logger.error(f"Falha na extração via Gemini: {gemini_result.get('error')}")
                step_failures.append(f"análise Gemini ({gemini_result.get('error')})")

            transcript_path = gemini_result.get("transcript_path")
            tema = gemini_result.get("tema")
            if not transcript_path:
                logger.warning(
                    "Transcrição do Gemini não disponível (falhou ou não foi gerada) - "
                    "o NotebookLM será criado só com slide e áudio, sem a fonte extra da transcrição."
                )

            # 2.5. Gera o .apkg de flashcards (Múltipla Escolha/Verdadeiro ou Falso,
            # mesmos note types do Anki do usuário) e salva no Drive. Independente do
            # NotebookLM - falha aqui não trava o resto do pipeline, só loga.
            flashcards = gemini_result.get("flashcards") or []
            if flashcards:
                apkg_path = drive_sync.resolve_apkg_output_path(unit_code, _safe_filename(lesson_name))
                apkg_result = build_flashcards_apkg(flashcards, unit_code, lesson_name, apkg_path)
                if apkg_result["success"]:
                    logger.info(
                        f".apkg de flashcards gerado em: {apkg_result['path']} "
                        f"({apkg_result['count_mc']} MC + {apkg_result['count_vf']} VF)"
                    )
                    # Backend local: arquivo já está na pasta certa do Drive Desktop
                    # (no-op). Backend cloud: sobe de verdade pro Drive via API.
                    publish_result = drive_sync.publish_flashcards_apkg(apkg_path, unit_code, lesson_name)
                    if publish_result["success"]:
                        logger.info(f".apkg publicado no Drive: {publish_result.get('url') or publish_result['path']}")
                    else:
                        logger.error(f"Falha ao publicar .apkg no Drive: {publish_result['error']}")
                        step_failures.append(f"publicação do .apkg no Drive ({publish_result['error']})")
                else:
                    logger.error(f"Falha ao gerar .apkg de flashcards: {apkg_result['error']}")
                    step_failures.append(f"geração de flashcards ({apkg_result['error']})")

                # 2.6. Sincronização AO VIVO com o Anki via AnkiConnect, quando
                # disponível (só funciona local, com o Anki aberto - no Cloud Run
                # simplesmente não está disponível e é pulada silenciosamente, sem
                # afetar o resultado do pipeline). Roda em paralelo ao .apkg, não
                # no lugar dele - o .apkg continua sendo a via garantida.
                anki_sync_result = anki_connect.sync_flashcards_to_anki(flashcards, unit_code, lesson_name)
                if anki_sync_result["available"] and not anki_sync_result["success"]:
                    step_failures.append(f"sincronização com o Anki ({anki_sync_result['error']})")
            elif gemini_result.get("success"):
                logger.warning("Gemini não retornou nenhum flashcard para esta aula - .apkg não foi gerado.")

            if create_result["success"]:
                # 3. Injeta as fontes no NotebookLM de uma vez (slide + transcrição
                # sempre; áudio bruto só no backend local). Fontes que já estão
                # indexadas num notebook reaproveitado são puladas
                # (existing_source_titles), pra não duplicar.
                #
                # Rodando na nuvem (STORAGE_BACKEND=cloud), o NotebookLM falha em
                # processar áudio vindo de IP de datacenter (bug real confirmado em
                # produção: mesmo arquivo, 0/N sucessos na nuvem vs sucesso imediato
                # local, em vários testes) - provável bloqueio/degradação por
                # anti-abuso, não corrigível do nosso lado. Em vez de tentar (e
                # sempre falhar) o áudio bruto, usa só a transcrição do Gemini como
                # fonte de texto - cobre o mesmo conteúdo informacional, e os
                # artefatos do Estúdio (inclusive "Áudio") são sintetizados a partir
                # do conteúdo das fontes, não dependem da fonte já ser áudio.
                is_cloud = os.environ.get("STORAGE_BACKEND", "local").strip().lower() == "cloud"
                extra_sources = [transcript_path] if transcript_path else []
                audio_sources = [] if is_cloud else audios
                if is_cloud and audios:
                    logger.info(
                        f"STORAGE_BACKEND=cloud - pulando upload do áudio bruto pro NotebookLM "
                        f"({[a.name for a in audios]}), usando só a transcrição do Gemini como fonte "
                        f"(áudio na nuvem tem falha conhecida de processamento no NotebookLM)."
                    )
                sources_result = notebooklm_client.add_sources_to_notebook(
                    notebook_id, slides + audio_sources + extra_sources, skip_titles=existing_source_titles
                )
                if not sources_result["success"]:
                    failed_files = [s["file"] for s in sources_result["sources"] if not s["success"]]
                    logger.error(f"Falha ao adicionar uma ou mais fontes ao NotebookLM: {failed_files}")
                    step_failures.append(f"fontes do NotebookLM ({', '.join(failed_files)})")

                # 3.5. Espera TODAS as fontes do notebook (inclusive fontes fantasma de
                # tentativas anteriores, se houver) saírem de "preparing/processando"
                # antes de gerar o Estúdio - senão a geração usa só as fontes que já
                # estavam prontas naquele instante, ignorando as que ainda faltavam
                # (bug real observado em produção: áudio ainda "preparing" e o Estúdio
                # sendo gerado mesmo assim, com resultado baseado em fonte incompleta).
                #
                # Se o timeout estourar com fontes ainda pendentes, NÃO apaga nem gera o
                # Estúdio nesta execução - áudio grande pode legitimamente demorar mais
                # que o timeout (não é sempre uma fonte "fantasma" de verdade), e apagar
                # uma fonte que só precisava de mais tempo destrói trabalho de indexação
                # que já estava em andamento. Em vez disso, marca como partial_failure e
                # deixa pro retry automático (próxima execução reaproveita o mesmo
                # notebook e só reconfere o status - se tiver terminado de indexar até lá,
                # gera o Estúdio completo; se ainda não, espera mais uma rodada).
                logger.info("Aguardando todas as fontes do NotebookLM ficarem prontas antes de gerar o Estúdio...")
                ready_result = notebooklm_client.wait_for_sources_ready(notebook_id)

                # Bug real observado em produção: quando o áudio é pulado na nuvem (acima) E
                # a transcrição do Gemini também falha por qualquer motivo, sobra só o slide
                # como fonte - "todas as fontes prontas" dá certo (não tem nada pendente/com
                # erro, só faltou ADICIONAR a transcrição), e o Estúdio era gerado mesmo assim,
                # baseado só no slide, perdendo todo o conteúdo do áudio silenciosamente. Só
                # bloqueia num notebook realmente novo (sem artefato nenhum ainda) - não impede
                # o retry de aproveitar um Estúdio já gerado numa tentativa anterior boa.
                missing_audio_content = is_cloud and bool(audios) and not transcript_path and not existing_artifact_types

                if ready_result["success"] and missing_audio_content:
                    logger.error(
                        "Áudio pulado na nuvem e a transcrição do Gemini não ficou disponível "
                        "(falhou nesta execução) - PULANDO a geração do Estúdio, pra não gerar "
                        "perdendo todo o conteúdo do áudio. O retry automático tenta de novo."
                    )
                    step_failures.append("transcrição do Gemini indisponível (áudio pulado na nuvem) - Estúdio não gerado")
                elif ready_result["success"]:
                    # 4. Dispara TODOS os artefatos do Estúdio (áudio, relatório, flashcards,
                    # teste, slides, vídeo, infográfico, tabela de dados, mapa mental) UMA
                    # ÚNICA VEZ, depois que todas as fontes já foram adicionadas. Artefatos
                    # já disparados num notebook reaproveitado são pulados, pra não duplicar.
                    studio_result = notebooklm_client.generate_studio_artifacts(
                        notebook_id, skip_types=existing_artifact_types
                    )
                    if not studio_result["success"]:
                        failed_artifacts = [k for k, r in studio_result["artifacts"].items() if not r["success"]]
                        logger.error(f"Falha ao gerar um ou mais artefatos do Estúdio: {failed_artifacts}")
                        step_failures.append(f"artefatos do Estúdio ({', '.join(failed_artifacts)})")
                elif ready_result.get("errored"):
                    # Fonte com ERRO terminal de processamento no NotebookLM (ex.: áudio que o
                    # backend nunca conseguiu processar) - diferente de "ainda processando",
                    # isso não se resolve esperando mais nem numa próxima tentativa com a MESMA
                    # fonte. Fica registrado como falha real - requer atenção manual (reenviar o
                    # arquivo, checar se está corrompido, etc.), não é auto-recuperável.
                    errored_titles = sorted({s.get("title") for s in ready_result["errored"] if s.get("title")})
                    logger.error(
                        f"{len(ready_result['errored'])} fonte(s) com ERRO terminal de processamento "
                        f"({errored_titles}) - PULANDO a geração do Estúdio, pra não gerar com fonte faltando. "
                        f"Requer atenção manual (não se resolve sozinho num retry)."
                    )
                    step_failures.append(f"fonte(s) com erro de processamento no NotebookLM ({', '.join(errored_titles)})")
                elif ready_result["pending"]:
                    stuck_titles = sorted({s.get("title") for s in ready_result["pending"] if s.get("title")})
                    logger.warning(
                        f"{len(ready_result['pending'])} fonte(s) ainda não terminaram de indexar após a espera "
                        f"({stuck_titles}) - PULANDO a geração do Estúdio nesta execução, pra não gerar com fonte "
                        f"incompleta. O retry automático reconfere o status na próxima rodada."
                    )
                    step_failures.append(f"fontes ainda processando, Estúdio não gerado nesta rodada ({', '.join(stuck_titles)})")
                else:
                    logger.error(f"Falha ao checar status das fontes do NotebookLM: {ready_result.get('error')} - pulando geração do Estúdio.")
                    step_failures.append(f"checagem de status das fontes ({ready_result.get('error')})")

                logger.info(f"NotebookLM pronto! Link: {notebook_url}")

                # 5. Registra o link na Planilha do Google Sheets (com o tema real do
                # slide, quando disponível, pra compor o nome de uma linha nova).
                sheets_ok = sheets_client.update_lesson_link(unit_code, lesson_name, notebook_url, tema=tema)
                if not sheets_ok:
                    logger.error(f"Falha ao registrar o link do NotebookLM na planilha (aba '{unit_code}').")
                    step_failures.append("registro na planilha do Google Sheets")

            # 6. Salva no banco de dados local com o status real do processamento
            status = "success" if not step_failures else "partial_failure"
            db_manager.mark_lesson_completed(
                unit_code=unit_code,
                lesson_name=lesson_name,
                notebook_id=notebook_id or "N/A",
                status=status,
                details="; ".join(step_failures) if step_failures else None,
            )

            if step_failures:
                logger.error(
                    f"Pipeline da aula {lesson_name} concluído COM FALHAS nas etapas: {'; '.join(step_failures)}"
                )
                return False

            logger.info(f"Pipeline concluído com sucesso para a aula {lesson_name}!")
            return True

        except Exception as e:
            logger.error(f"Erro crítico no orquestrador da aula {lesson_name}: {e}")
            return False

    def generate_more_flashcards(
        self, unit_code: str, lesson_name: str, lesson_folder: Union[str, Path], quantity: int = 10
    ) -> dict:
        """Gera `quantity` flashcards novos pra uma aula já processada (sob demanda,
        ex.: botão na UI do Streamlit) e regera o .apkg com o baralho COMPLETO
        (antigos + novos) - o Anki mescla pelo mesmo guid de sempre, então os
        cards antigos não duplicam, só os novos são acrescentados de fato.

        Diferente de process_lesson, não mexe no NotebookLM nem na planilha -
        só flashcards. Retorna {"success", "new_count", "total_count", "path", "error"}."""
        gemini_result = multimodal_processor.generate_more_flashcards(
            Path(lesson_folder), unit_code, lesson_name, quantity=quantity
        )
        if not gemini_result["success"]:
            return {"success": False, "new_count": 0, "total_count": 0, "path": None, "error": gemini_result["error"]}

        apkg_path = drive_sync.resolve_apkg_output_path(unit_code, _safe_filename(lesson_name))
        apkg_result = build_flashcards_apkg(gemini_result["all_flashcards"], unit_code, lesson_name, apkg_path)
        if not apkg_result["success"]:
            return {"success": False, "new_count": len(gemini_result["new_flashcards"]), "total_count": len(gemini_result["all_flashcards"]), "path": None, "error": apkg_result["error"]}

        publish_result = drive_sync.publish_flashcards_apkg(apkg_path, unit_code, lesson_name)
        if not publish_result["success"]:
            return {"success": False, "new_count": len(gemini_result["new_flashcards"]), "total_count": len(gemini_result["all_flashcards"]), "path": None, "error": publish_result["error"]}

        return {
            "success": True,
            "new_count": len(gemini_result["new_flashcards"]),
            "total_count": len(gemini_result["all_flashcards"]),
            "path": publish_result.get("url") or apkg_result["path"],
            "error": None,
        }


orchestrator = Orchestrator()
