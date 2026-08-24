from pathlib import Path
import tempfile
import streamlit as st

from core.orchestrator import orchestrator
from database.db import db_manager
from core.drive_sync import drive_sync

st.set_page_config(
    page_title="MedStudy Automator",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        color: #0284c7;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1.1rem;
        color: #64748b;
        margin-bottom: 1.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Sidebar
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/caduceus.png", width=64)
    st.title("MedStudy Dashboard")
    
    stats = db_manager.get_total_stats()
    st.metric("Total de Aulas Processadas", f"{stats['total_lessons']}")
    st.metric("Flashcards Gerados", f"{stats['total_cards']}")
    st.metric("Tokens Consumidos", f"{stats['total_prompt_tokens'] + stats['total_completion_tokens']:,}")
    st.divider()
    st.caption("Automação: Drive Local ➔ Gemini Flash ➔ Anki & Resumos.")

tab_drive, tab_process, tab_history = st.tabs([
    "📁 Pastas do Google Drive",
    "🚀 Processar Arquivo Avulso",
    "📚 Histórico & Baralhos"
])

# ABA 1: VARREDURA DIRETA DO GOOGLE DRIVE
with tab_drive:
    st.markdown('<div class="main-title">Varredura do Google Drive</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">O sistema detecta automaticamente as UCs, Aulas e Arquivos da sua pasta local.</div>', unsafe_allow_html=True)

    col_dir, col_btn = st.columns([3, 1])
    with col_dir:
        drive_folder_input = st.text_input(
            "📁 Pasta Raiz das Aulas no Computador",
            value=str(drive_sync.drive_path),
            placeholder="Ex: G:\\Meu Drive\\MedStudy_Aulas",
        )
        base_path = Path(drive_folder_input)
        if base_path.exists():
            drive_sync.drive_path = base_path
            st.caption(" Conectado à pasta local.")
        else:
            st.caption("⚠️ Pasta não encontrada no disco.")

    with col_btn:
        st.write("")
        st.write("")
        if st.button("🔄 Rastrear Pastas", use_container_width=True):
            st.rerun()

    # Varre a pasta
    scanned_data = drive_sync.scan_ucs_and_lessons(base_path)

    if not scanned_data:
        st.info(f"Nenhuma subpasta de UC encontrada dentro de `{drive_folder_input}`. Crie pastas como `UC16`, `UC04`, `UC29` com suas aulas dentro.")
    else:
        uc_list = list(scanned_data.keys())
        selected_uc = st.selectbox("Selecione a Unidade Curricular (detectada na pasta)", uc_list)
        
        lessons_found = scanned_data.get(selected_uc, [])
        st.write(f"### Aulas encontradas em **{selected_uc}** ({len(lessons_found)} aulas detectadas)")

        for i, item in enumerate(lessons_found):
            with st.expander(f"📁 {item['lesson_title']} ({item['total_files']} arquivo(s))", expanded=True):
                col_s, col_a = st.columns(2)
                
                with col_s:
                    if item["slide"]:
                        st.success(f"📄 Slide detectado: `{item['slide'].name}`")
                    else:
                        st.warning("📄 Nenhum slide (.pdf) na pasta da aula.")
                
                with col_a:
                    if item["audio"]:
                        st.success(f"🎙️ Áudio detectado: `{item['audio'].name}`")
                    else:
                        st.info("🎙️ Nenhum áudio na pasta da aula.")

                if st.button(f"⚡ Processar e Gerar Baralho Anki ({item['lesson_title']})", key=f"btn_scan_{selected_uc}_{i}", type="primary"):
                    if not item["slide"] and not item["audio"]:
                        st.error("Adicione ao menos um PDF ou áudio dentro desta subpasta para processar.")
                    else:
                        with st.spinner(f"Processando {selected_uc} - {item['lesson_title']} com Gemini Flash e Anki..."):
                            res = orchestrator.process_lesson(
                                unit_code=selected_uc,
                                lesson_name=item["lesson_title"],
                                slide_path=item["slide"],
                                audio_path=item["audio"],
                                force_reprocess=True,
                                sync_anki=True,
                            )
                            st.success(f" Aula processada com sucesso! {res['cards_count']} cards gerados.")
                            if Path(res["apkg_path"]).exists():
                                with open(res["apkg_path"], "rb") as f:
                                    st.download_button(
                                        "📥 Baixar Baralho Anki (.apkg)",
                                        data=f.read(),
                                        file_name=Path(res["apkg_path"]).name,
                                        key=f"dl_scan_{selected_uc}_{i}",
                                    )

# ABA 2: PROCESSAMENTO INDIVIDUAL
with tab_process:
    st.markdown('<div class="main-title">Processador Avulso</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Envie qualquer slide ou gravação individual para testar.</div>', unsafe_allow_html=True)

    col_meta1, col_meta2 = st.columns(2)
    with col_meta1:
        unit_code = st.text_input("Unidade Curricular / Especialidade", placeholder="ex: UC16")
    with col_meta2:
        lesson_name = st.text_input("Título do Tema / Aula", placeholder="ex: Infecções de Vias Aéreas Superiores")

    col_up1, col_up2 = st.columns(2)
    with col_up1:
        uploaded_slide = st.file_uploader("Slide da Aula (PDF)", type=["pdf"], key="single_slide")
    with col_up2:
        uploaded_audio = st.file_uploader("Gravação de Áudio (Opcional)", type=["mp3", "m4a", "wav"], key="single_audio")

    if st.button("✨ Gerar Flashcards", type="primary", use_container_width=True, key="btn_single"):
        if not unit_code or not lesson_name:
            st.error("Preencha a Unidade e o Título da Aula.")
        elif not uploaded_slide and not uploaded_audio:
            st.error("Anexe ao menos um arquivo de slide ou áudio.")
        else:
            with st.spinner("Processando com Gemini Flash..."):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    s_path = Path(tmp_dir) / uploaded_slide.name if uploaded_slide else None
                    if s_path:
                        s_path.write_bytes(uploaded_slide.read())
                    a_path = Path(tmp_dir) / uploaded_audio.name if uploaded_audio else None
                    if a_path:
                        a_path.write_bytes(uploaded_audio.read())

                    res = orchestrator.process_lesson(
                        unit_code=unit_code.strip(),
                        lesson_name=lesson_name.strip(),
                        slide_path=s_path,
                        audio_path=a_path,
                        force_reprocess=True,
                        sync_anki=True,
                    )
                    st.success(f"Concluído em {res['execution_time']:.2f}s! ({res['cards_count']} cards)")
                    if Path(res["apkg_path"]).exists():
                        with open(res["apkg_path"], "rb") as f:
                            st.download_button(
                                "📥 Baixar Baralho Anki (.apkg)",
                                data=f.read(),
                                file_name=Path(res["apkg_path"]).name,
                                use_container_width=True,
                            )

# ABA 3: HISTÓRICO
with tab_history:
    st.subheader("📚 Aulas Processadas no Banco Local")
    import sqlite3
    conn = sqlite3.connect(db_manager.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, unit_code, lesson_name, cards_count, created_at, apkg_path FROM lessons ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        st.info("Nenhuma aula registrada ainda.")
    else:
        for r in rows:
            with st.container():
                col_c1, col_c2, col_c3 = st.columns([3, 1, 1])
                with col_c1:
                    st.markdown(f"**[{r[1]}] {r[2]}**")
                    st.caption(f"Registrado em: {r[4][:19]}")
                with col_c2:
                    st.markdown(f"🎴 **{r[3]} cards**")
                with col_c3:
                    if r[5] and Path(r[5]).exists():
                        with open(r[5], "rb") as f:
                            st.download_button(
                                "📥 Baixar",
                                data=f.read(),
                                file_name=Path(r[5]).name,
                                key=f"dl_hist_{r[0]}",
                            )
                st.divider()