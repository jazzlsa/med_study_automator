import streamlit as st
from pathlib import Path
from core.orchestrator import orchestrator
from database.db import db_manager
from core.drive_sync import drive_sync
from core.sheets_sync import AVAILABLE_UCS

st.set_page_config(
    page_title="MedStudy Automator - NotebookLM",
    page_icon="🧠",
    layout="wide"
)

st.title("🧠 MedStudy Automator: Pipeline NotebookLM")
st.markdown("Automatize a criação de workspaces no NotebookLM, transcrições e resumos clínicos das suas aulas da USP.")

# Sidebar para seleção de unidades e aulas
st.sidebar.header("📁 Seleção de Aulas")

# Lista de UCs centralizada em core/sheets_sync.py (mesmas abas reais da planilha
# de controle), pra não divergir de UC pra UC entre a UI e o script de automação.
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
        
        slide_file = lesson_selection.get("slide")
        audio_file = lesson_selection.get("audio")

        st.markdown("---")
        st.markdown("#### 📂 Arquivos Detectados:")
        if slide_file and Path(slide_file).exists():
            st.success(f"Slide encontrado: `{Path(slide_file).name}`")
        else:
            st.warning("Nenhum slide (.pdf) encontrado na pasta desta aula.")

        if audio_file and Path(audio_file).exists():
            st.success(f"Áudio encontrado: `{Path(audio_file).name}`")
            st.audio(str(audio_file))
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