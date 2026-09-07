# Política de Privacidade — MedStudy Automator

_Última atualização: 2026-09-07_

O MedStudy Automator é uma ferramenta pessoal de automação de estudos, usada por um
único usuário para o próprio fluxo de estudo (transcrição de aulas e geração de
flashcards). Não é distribuído como produto para terceiros.

## Dados acessados

- **Google Drive**: o app lê arquivos de áudio/vídeo/slides em uma pasta específica do
  Google Drive do usuário, para identificar aulas novas e processá-las. Não acessa
  outras pastas nem compartilha esses arquivos com terceiros.
- **YouTube (canal Visão Libertária)**: o app faz upload de vídeos como "não listado" e
  consulta/edita metadados (thumbnail, monetização) desse canal.

## Uso dos dados

Os dados lidos (áudio, slides) são enviados a APIs de IA (Google Gemini e Anthropic
Claude) apenas para gerar transcrição e flashcards de estudo. Nenhum dado é vendido,
compartilhado publicamente ou usado para fins além do funcionamento do próprio pipeline.

## Armazenamento

Os arquivos processados e os tokens de acesso ficam apenas na máquina do usuário
(computador pessoal e Raspberry Pi de uso próprio). Nenhum backend de terceiros
armazena esses dados além dos provedores de API citados acima, durante o processamento.

## Contato

Dúvidas sobre esta política: abra uma issue no repositório
[jazzlsa/med_study_automator](https://github.com/jazzlsa/med_study_automator).
