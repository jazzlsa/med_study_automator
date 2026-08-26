import streamlit as st
from pathlib import Path
from core.orchestrator import orchestrator
from database.db import db_manager
from core.drive_sync import drive_sync
from config.settings import settings, save_semester_config

st.set_page_config(
    page_title="MedStudy Automator - NotebookLM",
    page_icon="🧠",
    layout="wide"
)

st.title("🧠 MedStudy Automator: Pipeline NotebookLM")
st.markdown("Automatize a criação de workspaces no NotebookLM, transcrições e resumos clínicos das suas aulas da USP.")

tab_pipeline, tab_config = st.tabs(["📋 Pipeline", "⚙️ Configurações"])

with tab_config:
    st.subheader("⚙️ Configurações do semestre")
    st.caption(
        "Tudo que muda de semestre pra semestre (planilha nova, pastas novas do Drive, "
        "UCs cursadas) fica aqui - salva direto em config/config.yaml, sem precisar mexer em código."
    )

    cfg_spreadsheet_id = st.text_input(
        "ID da planilha do Google Sheets", value=settings.semester.spreadsheet_id or "",
        help="A parte do link entre /d/ e /edit, ex.: docs.google.com/spreadsheets/d/ESSE_ID_AQUI/edit",
    )
    cfg_lessons_folder = st.text_input(
        "Nome da pasta de aulas no Drive", value=settings.semester.drive_lessons_folder_name,
        help="Pasta dentro de 'Meu Drive' onde ficam as aulas (ex.: MedStudy_Aulas)",
    )
    cfg_flashcards_folder = st.text_input(
        "Nome da pasta de flashcards no Drive", value=settings.semester.drive_flashcards_folder_name,
        help="Pasta dentro de 'Meu Drive' onde os .apkg são salvos (ex.: MedStudy_Flashcards)",
    )
    cfg_ucs_text = st.text_area(
        "UCs deste semestre (uma por linha, igual ao nome da aba na planilha)",
        value="\n".join(settings.semester.available_ucs),
        height=200,
    )

    if st.button("💾 Salvar configurações", type="primary"):
        new_ucs = [line.strip() for line in cfg_ucs_text.splitlines() if line.strip()]
        save_semester_config(
            spreadsheet_id=cfg_spreadsheet_id.strip(),
            drive_lessons_folder_name=cfg_lessons_folder.strip(),
            drive_flashcards_folder_name=cfg_flashcards_folder.strip(),
            available_ucs=new_ucs,
        )
        st.success("Configurações salvas em config/config.yaml! Recarregando...")
        st.rerun()

    st.markdown("---")
    st.caption(
        "⚠️ Se você mudar o nome da pasta de flashcards, lembre de atualizar também o "
        "`WATCH_FOLDER` no addon do Anki (`medstudy_auto_import/__init__.py`) - ele fica "
        "fora deste repositório e não é atualizado automaticamente por aqui."
    )

with tab_pipeline:
    # Sidebar para seleção de unidades e aulas
    st.sidebar.header("📁 Seleção de Aulas")

    # UCs do semestre atual (config/config.yaml, editável na aba Configurações) -
    # mesma fonte usada pelo script de automação, pra não divergir entre os dois.
    AVAILABLE_UCS = settings.semester.available_ucs
    selected_uc = st.sidebar.selectbox("Selecione a Unidade Curricular:", AVAILABLE_UCS)

    # Busca aulas disponíveis no diretório do Drive local
    lessons = drive_sync.scan_local_lessons(selected_uc)

    if not lessons:
        st.sidebar.warning(f"Nenhuma aula encontrada para {selected_uc} na pasta padrão.")
        lesson_selection = None
    else:
        lesson_titles = [l["lesson_title"] for l in lessons]
        selected_lesson_title = st.sidebar.selectbox("Selecione a Aula:", lesson_titles)
        lesson_selection = next((l for l in lessons if l["lesson_title"] == selected_lesson_title), None)

    # Layout principal
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("📋 Detalhes da Aula e Materiais")
        if lesson_selection:
            st.write(f"**Unidade:** {selected_uc}")
            st.write(f"**Aula:** {lesson_selection['lesson_title']}")

            # "slide"/"audio" vêm como lista agora (uma aula pode ter o áudio
            # dividido em várias partes) - filtra só os que existem de verdade.
            slide_files = [f for f in (lesson_selection.get("slide") or []) if f and Path(f).exists()]
            audio_files = [f for f in (lesson_selection.get("audio") or []) if f and Path(f).exists()]

            st.markdown("---")
            st.markdown("#### 📂 Arquivos Detectados:")
            if slide_files:
                for f in slide_files:
                    st.success(f"Slide encontrado: `{Path(f).name}`")
            else:
                st.warning("Nenhum slide (.pdf) encontrado na pasta desta aula.")

            if audio_files:
                for f in audio_files:
                    st.success(f"Áudio encontrado: `{Path(f).name}`")
                    st.audio(f)
            else:
                st.info("Nenhum áudio (.mp3) encontrado na pasta desta aula (opcional).")

    with col2:
        st.subheader("⚡ Ações de Automação")
        if lesson_selection:
            btn_process = st.button("🚀 Processar Aula e Criar NotebookLM", type="primary", use_container_width=True)

            if btn_process:
                with st.spinner(f"Processando [{selected_uc}] {lesson_selection['lesson_title']}..."):
                    success = orchestrator.process_lesson(
                        unit_code=selected_uc,
                        lesson_name=lesson_selection["lesson_title"],
                        slide_path=lesson_selection.get("slide"),
                        audio_path=lesson_selection.get("audio")
                    )

                    if success:
                        st.success("🎉 Aula processada com sucesso! NotebookLM criado e transcrição salva.")
                        st.balloons()
                    else:
                        st.error("Ocorreu um erro durante o processamento. Verifique os logs do terminal.")

            st.markdown("---")
            st.markdown("#### ➕ Mais flashcards")
            st.caption("Gera flashcards extras pra essa aula (precisa já ter sido processada antes, com áudio).")
            qty = st.number_input("Quantos flashcards a mais?", min_value=1, max_value=30, value=10, step=1)
            btn_more = st.button("Gerar mais flashcards", use_container_width=True)

            if btn_more:
                with st.spinner(f"Gerando {int(qty)} flashcards novos pra '{lesson_selection['lesson_title']}'..."):
                    more_result = orchestrator.generate_more_flashcards(
                        unit_code=selected_uc,
                        lesson_name=lesson_selection["lesson_title"],
                        lesson_folder=lesson_selection["folder_path"],
                        quantity=int(qty),
                    )
                if more_result["success"]:
                    st.success(
                        f"🎉 {more_result['new_count']} flashcards novos gerados "
                        f"(total agora: {more_result['total_count']}). .apkg atualizado."
                    )
                else:
                    st.error(f"Erro ao gerar mais flashcards: {more_result['error']}")

    st.markdown("---")
    st.subheader("📊 Histórico de Aulas Processadas")
    completed_lessons = db_manager.get_completed_lessons(selected_uc)
    if completed_lessons:
        for item in completed_lessons:
            status = item.get("status", "success")
            icon = "✅" if status == "success" else "⚠️"
            line = f"- {icon} **{item['lesson_name']}** (Notebook ID: `{item.get('notebook_id', 'N/A')}`)"
            if status != "success" and item.get("details"):
                line += f" — _{item['details']}_"
            st.markdown(line)
    else:
        st.info("Nenhuma aula processada para esta unidade ainda.")