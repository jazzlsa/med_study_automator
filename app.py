from pathlib import Path
import tempfile
import streamlit as st

from core.drive_sync import drive_sync
from core.orchestrator import orchestrator
from core.sheets_sync import AVAILABLE_UCS, SPREADSHEET_ID, sheets_sync
from database.db import db_manager

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
  st.metric("Total de Aulas", f"{stats['total_lessons']}")
  st.metric("Flashcards Gerados", f"{stats['total_cards']}")
  st.metric(
      "Tokens Consumidos",
      f"{stats['total_prompt_tokens'] + stats['total_completion_tokens']:,}",
  )

  st.divider()
  st.caption("Pipeline Multimodal com Gemini Flash e Anki Integrado.")

tab_sheets, tab_process, tab_history, tab_settings = st.tabs([
    "📊 Planilha & Google Drive",
    "🚀 Processar Individual",
    "📚 Histórico & Baralhos",
    "⚙️ Configurações",
])


# Cache de aulas por UC
@st.cache_data(ttl=60)
def load_lessons(uc: str):
  return sheets_sync.fetch_lessons_for_uc(uc)


# ABA 1: PLANILHA E GOOGLE DRIVE
with tab_sheets:
  st.markdown(
      '<div class="main-title">Automação de UCs (Planilha + Drive)</div>',
      unsafe_allow_html=True,
  )
  st.markdown(
      '<div class="sub-title">Aulas mapeadas do Google Sheets integradas à'
      " pasta de arquivos do Google Drive.</div>",
      unsafe_allow_html=True,
  )

  col_drive, col_uc, col_refresh = st.columns([2, 1, 0.6])
  with col_drive:
    drive_folder_input = st.text_input(
        "📁 Caminho da Pasta do Google Drive",
        value=str(drive_sync.drive_path),
        placeholder="Ex: G:\\Meu Drive\\MedStudy_Aulas",
    )
    if Path(drive_folder_input).exists():
      drive_sync.drive_path = Path(drive_folder_input)
      st.caption(" Conectado à pasta local do Drive.")
    else:
      st.caption("⚠️ Pasta não encontrada no disco.")

  with col_uc:
    selected_uc = st.selectbox(
        "Selecione a Unidade Curricular", AVAILABLE_UCS, index=0
    )

  with col_refresh:
    st.write("")
    st.write("")
    if st.button("🔄 Atualizar"):
      st.cache_data.clear()
      st.rerun()

  lessons = load_lessons(selected_uc)

  if lessons:
    st.write(
        f"### Aulas da **{selected_uc}** ({len(lessons)} identificadas na"
        " planilha)"
    )
    for i, l in enumerate(lessons):
      with st.expander(
          f"📖 {l['lesson_name']}"
          + (f" — {l['author']}" if l["author"] else ""),
          expanded=False,
      ):
        if l["notebooklm_link"]:
          st.write(
              f"**Link NotebookLM:** [{l['notebooklm_link']}]({l['notebooklm_link']})"
          )

        # Busca arquivos correspondentes no Drive
        found_files = drive_sync.find_lesson_files(
            selected_uc, l["lesson_name"]
        )

        col_info_s, col_info_a = st.columns(2)
        with col_info_s:
          if found_files["slide"]:
            st.success(f"📄 Slide no Drive: `{found_files['slide'].name}`")
          else:
            st.info("📄 Slide não localizado na pasta.")
          slide_up = st.file_uploader(
              "Substituir / Anexar Slide (PDF)",
              type=["pdf"],
              key=f"slide_uc_{selected_uc}_{i}",
          )

        with col_info_a:
          if found_files["audio"]:
            st.success(f"🎙️ Áudio no Drive: `{found_files['audio'].name}`")
          else:
            st.info("🎙️ Áudio não localizado na pasta.")
          audio_up = st.file_uploader(
              "Substituir / Anexar Áudio",
              type=["mp3", "m4a", "wav"],
              key=f"audio_uc_{selected_uc}_{i}",
          )

        if st.button(
            f"⚡ Processar Aula ({l['lesson_name'][:25]}...)",
            key=f"btn_proc_{selected_uc}_{i}",
            type="primary",
        ):
          final_slide = None
          final_audio = None

          with tempfile.TemporaryDirectory() as tmp_dir:
            if slide_up:
              final_slide = Path(tmp_dir) / slide_up.name
              final_slide.write_bytes(slide_up.read())
            elif found_files["slide"]:
              final_slide = found_files["slide"]

            if audio_up:
              final_audio = Path(tmp_dir) / audio_up.name
              final_audio.write_bytes(audio_up.read())
            elif found_files["audio"]:
              final_audio = found_files["audio"]

            if not final_slide and not final_audio:
              st.error(
                  "Nenhum slide ou áudio encontrado na pasta ou anexado."
              )
            else:
              with st.spinner("Processando com Gemini Flash e Anki..."):
                res = orchestrator.process_lesson(
                    unit_code=selected_uc,
                    lesson_name=l["lesson_name"],
                    slide_path=final_slide,
                    audio_path=final_audio,
                    force_reprocess=True,
                    sync_anki=True,
                )
                st.success(
                    "Aula processada com sucesso!"
                    f" {res['cards_count']} cards gerados."
                )
                if Path(res["apkg_path"]).exists():
                  with open(res["apkg_path"], "rb") as f:
                    st.download_button(
                        "📥 Baixar Baralho Anki (.apkg)",
                        data=f.read(),
                        file_name=Path(res["apkg_path"]).name,
                        key=f"dl_uc_{selected_uc}_{i}",
                    )
  else:
    st.info(
        f"Nenhuma aula com título preenchido na aba '{selected_uc}' ou aba"
        " vazia na planilha."
    )

# ABA 2: PROCESSAMENTO INDIVIDUAL
with tab_process:
  st.markdown(
      '<div class="main-title">Processador Individual</div>',
      unsafe_allow_html=True,
  )
  st.markdown(
      '<div class="sub-title">Envie qualquer slide em PDF ou gravação'
      " isolada.</div>",
      unsafe_allow_html=True,
  )

  col_meta1, col_meta2 = st.columns(2)
  with col_meta1:
    unit_code = st.text_input(
        "Unidade Curricular / Especialidade", placeholder="ex: UC02, Nefrologia"
    )
  with col_meta2:
    lesson_name = st.text_input(
        "Título do Tema / Aula", placeholder="ex: Cetoacidose Diabética"
    )

  col_up1, col_up2 = st.columns(2)
  with col_up1:
    uploaded_slide = st.file_uploader(
        "Slide da Aula (PDF)", type=["pdf"], key="single_slide"
    )
  with col_up2:
    uploaded_audio = st.file_uploader(
        "Gravação de Áudio (Opcional)",
        type=["mp3", "m4a", "wav"],
        key="single_audio",
    )

  if st.button(
      "✨ Gerar Flashcards",
      type="primary",
      use_container_width=True,
      key="btn_single",
  ):
    if not unit_code or not lesson_name:
      st.error("Preencha a Unidade e o Título da Aula.")
    elif not uploaded_slide and not uploaded_audio:
      st.error("Anexe ao menos um arquivo de slide ou áudio.")
    else:
      with st.spinner("Processando com Gemini Flash..."):
        with tempfile.TemporaryDirectory() as tmp_dir:
          s_path = (
              Path(tmp_dir) / uploaded_slide.name if uploaded_slide else None
          )
          if s_path:
            s_path.write_bytes(uploaded_slide.read())
          a_path = (
              Path(tmp_dir) / uploaded_audio.name if uploaded_audio else None
          )
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
          st.success(
              f"Concluído em {res['execution_time']:.2f}s!"
              f" ({res['cards_count']} cards)"
          )
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
  cursor.execute(
      "SELECT id, unit_code, lesson_name, cards_count, created_at, apkg_path"
      " FROM lessons ORDER BY id DESC"
  )
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

# ABA 4: CONFIGURAÇÕES
with tab_settings:
  st.subheader("⚙️ Configurações do Sistema")
  st.text_input("Gemini Model", value="gemini-3.6-flash", disabled=True)
  st.text_input("AnkiConnect URL", value="http://localhost:8765", disabled=True)
  st.text_input("ID da Planilha Google", value=SPREADSHEET_ID, disabled=True)