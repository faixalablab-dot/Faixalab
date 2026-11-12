# FaixaLab 0.10.1

Aplicativo independente para Windows ou servidor Linux: link YouTube ou arquivo MP3, separação de instrumentos, cifras, letra sincronizada e tablatura da guitarra solo. A importação padrão usa um fluxo geral para todas as músicas. Não há regras por título, artista, ID de vídeo ou posições fixas de solos.

## Correção de reprodução — playback1

O player mantém o carregamento das faixas ativas durante uma pausa por falta de dados, aguarda até dois segundos de áudio disponível antes de retomar e preserva os bytes já carregados. Uma interrupção transitória deixa de provocar recargas periódicas do arquivo. Os canais silenciados continuam sem decodificação e passam a reduzir também seu preload. Os limites e as verificações estão em **VALIDACAO_REPRODUCAO_0_10_1.md**.

Quem já possui a 0.10.1 pode aplicar somente `FaixaLab_0_10_1_Correcao_Reproducao.zip`: feche o aplicativo, extraia diretamente na pasta que contém `FaixaLab.exe`, substitua os arquivos e reabra o projeto concluído. A correção contém apenas os arquivos da interface; não reinstala Python, não baixa pesos e não repete a separação.

## Importação do YouTube — correção 0.10.1

O download exige yt-dlp atualizado, EJS correspondente e Deno 2.3+ ou Node 22+. O instalador agora valida o executável e sua versão; se nenhum runtime compatível estiver disponível, prepara um Deno privado também no Linux. Um Deno antigo no PATH deixa de esconder o runtime privado ou impedir o uso de Node compatível. O download independe do diretório em que o aplicativo foi iniciado.

Para atualizar uma instalação 0.10: feche o FaixaLab, extraia o novo ZIP na pasta existente substituindo os arquivos e execute `ATUALIZAR_YOUTUBE.bat`; depois abra `FaixaLab.exe`. Essa atualização reaproveita o perfil dos motores de áudio da 0.10. Projetos e cache ficam fora do pacote, em `%LOCALAPPDATA%\FaixaLab`. Em Linux, execute `bash cloud/atualizar_youtube.sh` e reinicie o servidor.

Erros não exibem sequências ANSI de terminal. Cada tentativa salva as versões, o runtime e os avisos em `youtube-diagnostico.json`, na pasta do projeto. A mensagem genérica `This video is not available` não é tratada como comprovação de vídeo removido. Restrições explícitas de acesso continuam sendo apresentadas. Os testes e os limites da correção estão em **VALIDACAO_0_10_1.md**.

## Otimização do processamento — versão 0.10

- O processamento deixa de limitar todos os computadores a quatro threads. O modo automático usa até oito, respeitando afinidade e cotas de CPU do contêiner. `FAIXALAB_CPU_THREADS` permite fixar um orçamento menor; um Codespace de dois núcleos continua limitado a dois.
- O instalador detecta a GPU NVIDIA e o driver existentes, escolhe um runtime PyTorch CUDA compatível e testa kernels reais. Sem suporte, prepara CPU. Falhas recuperáveis de memória CUDA durante a separação recebem uma única tentativa em CPU. A instalação não cria máquinas nem contrata recursos na nuvem.
- A atenção dos dois Roformers usa a seleção de kernels do próprio PyTorch; foi removido um argumento incompatível com o runtime 2.5.1. A inferência permanece float32, com os mesmos pesos e as mesmas janelas.
- A comparação de sobreposições lê cada faixa em blocos uma vez e reutiliza suas correlações. Os limiares e as regras de seleção permanecem iguais; casos em que o erro de arredondamento pode mudar uma decisão voltam ao cálculo original.
- A verificação de timbres usa até quatro contextos independentes em máquinas com orçamento de oito threads, preservando entradas, ordem e todos os rótulos. Em duas threads continua sequencial.
- O código nativo do DSP pode ser reaproveitado entre análises. O cache é separado por instalação, versão, ambiente e dependências; o Numba ainda confere a compatibilidade da CPU. Esse cache não acompanha o ZIP.
- Quando o refinamento não mantém uma faixa de voz, a letra usa uma voz isolada ainda disponível no histórico do projeto antes de recorrer à mistura original. Não se desativa o reconhecedor nem se troca seu modelo para acelerar.
- `project.json` registra os tempos de cada etapa em `performance`. O refinamento também informa tempos internos em `engine.instrument_refinement.timings_seconds`, para diagnosticar outras músicas sem regras específicas para elas.

Os 33 candidatos automáticos, a seleção manual, os pesos dos modelos, os critérios da cifra e a detecção de solo continuam disponíveis. O ganho depende do hardware. Resultados medidos, comparação do áudio e limites estão em **VALIDACAO.md**. Não há benchmark direto que estabeleça equivalência ou superioridade ao Moises.

Para medir um projeto já processado, use **Separar novamente as faixas** ou **Refinar instrumentos por IA**. Importar o mesmo arquivo/link com as mesmas opções pode reaproveitar o projeto pronto, como antes.

## Reprodução e entrega das faixas — versão 0.9

- O refinamento pode concluir enquanto você ouve a música. O áudio em reprodução continua com a mesma revisão; o aviso **Novas faixas prontas** permite aplicar o resultado com **Atualizar faixas**, mantendo a posição e os volumes/M/S das faixas existentes. Ao pausar e tocar novamente, o mixer adota a revisão mais recente.
- Cada URL de faixa identifica a revisão dos arquivos. Requisições de novos trechos do áudio continuam lendo os mesmos bytes, mesmo após a separação de solo/base ou outro refinamento.
- Faixas silenciadas deixam de ser decodificadas desnecessariamente. Ao ativá-las novamente, o player as posiciona no tempo atual antes de reproduzir.
- O sincronismo aguarda uma faixa terminar de carregar/buscar antes de corrigir seu tempo. Há recuperação limitada para interrupções e um estado visível quando o áudio não responde.
- No aplicativo local Windows, o mixer reproduz o WAV salvo diretamente. A conversão AAC/M4A fica para acesso remoto e backing tracks.
- A verificação de timbres processa dois contextos independentes em paralelo quando o orçamento é de quatro threads; com duas threads, continua sequencial. Os mesmos 33 candidatos, pesos, entradas, limiares e janelas permanecem avaliados.
- Os subprocessos de áudio executam com prioridade inferior à interface quando o sistema oferece esse recurso.

Em sete controles gravados de 60 s, a verificação de timbres levou 2,222 s na versão anterior e 1,164 s nesta revisão, com scores idênticos. Essa redução de 47,6% pertence à verificação de timbres, não ao tempo total de separação. A duração completa ainda depende da música e da máquina. Medições e testes estão em **VALIDACAO.md**.

## Usar pelo celular com o PC desligado

A configuração de GitHub Codespaces fica em [faixalablab-dot/mvlab](https://github.com/faixalablab-dot/mvlab). O servidor executa o código de `app/`. O manifesto `release.json` identifica o pacote de modelos binários.

1. Abra um Codespace do repositório, na máquina de **2 núcleos / 8 GB**.
2. Aguarde a instalação automática. A primeira instalação baixa dependências e, no primeiro processamento, os modelos maiores ainda ausentes.
3. Na aba **Ports**, abra a porta **7860**. A porta permanece privada e exige a sua sessão do GitHub, inclusive no celular.
4. Cole um link do YouTube ou envie o MP3. O processamento acontece no GitHub; seu PC pode ficar desligado.
5. Abra **Backing track**, escolha remover voz/solo ou usar os volumes M/S, prepare e toque em **Ouvir backing track**. Para usar sem conexão, baixe o M4A.
6. Conecte a caixa pelas configurações Bluetooth do celular. O navegador usa a saída de áudio do aparelho.
7. Ao terminar, pare o Codespace. Ele pode ser retomado pelo celular; os dados permanecem enquanto a instância existir.

A franquia pessoal GitHub Free consultada em 07/11/2026 é de **120 horas-núcleo/mês**, equivalentes a cerca de **60 horas em 2 núcleos**, e **15 GB-mês de armazenamento**. Essas cotas são compartilhadas com os outros Codespaces da conta. O armazenamento continua contando com a instância parada. A configuração não contrata GPU, aumenta orçamento ou habilita cobrança. Antes de criar a instância, confira a franquia restante e o bloqueio de gastos da conta.

Codespaces é ambiente de desenvolvimento: há suspensão por inatividade (30 min por padrão) e limite de 12 horas por sessão. Não oferece servidor permanente, GPU gratuita ou disponibilidade contínua. A CPU de dois núcleos pode ser mais lenta que seu computador. O acesso a vídeos do YouTube também pode ser recusado para IPs de datacenter; o envio de MP3 continua disponível.

Referências: [franquias e armazenamento](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces), [ciclo e inatividade](https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces).

## O que mudou na velocidade e reprodução

- Os 33 candidatos de instrumentos continuam sendo avaliados. As camadas das máscaras agora executam em lote, sem trocar pesos ou reduzir categorias.
- O detector semântico de solo pula apenas contextos que não podem influenciar trechos elegíveis pelos critérios já existentes. Os limiares e a confirmação de guitarra continuam iguais.
- Notas brutas de cada trecho ficam em cache. Alterar vozes ou limites de casas reaproveita a inferência e recalcula a digitação. Trocar o áudio, a revisão ou o modelo invalida o cache. Correções manuais e exclusões continuam aplicadas depois da leitura do cache.
- Links iguais do YouTube e arquivos com o mesmo SHA-256, com as mesmas opções, reutilizam o projeto pronto ou em processamento. Um projeto com seleção manual de instrumentos não é reutilizado dessa forma.
- O mixer transmite AAC/M4A sob demanda. O botão **Backing track** prepara um único arquivo para reprodução pelo celular; os WAVs continuam disponíveis para exportação.
- A interface possui layout para telas estreitas, controles de toque, manifesto instalável e controles de mídia compatíveis com o navegador. O cache do aplicativo inclui somente a interface; ouvir uma backing track offline exige baixá-la.

As medições desta revisão e suas limitações estão em **VALIDACAO.md**.

## Abrir e atualizar

1. Feche a versão anterior e extraia o ZIP inteiro para uma pasta nova.
2. Abra **FaixaLab.exe**. Mantenha o executável e todos os arquivos do pacote juntos.
3. Seus projetos continuam em `%LOCALAPPDATA%\FaixaLab` e aparecem automaticamente.

O iniciador verifica o perfil 0.10.1 em `.runtime/ready` e prepara um Python privado. Os modelos de acordes, detector de solo MERT, Basic Pitch, reconhecedor de timbres EfficientAT e dicionário acompanham o pacote. O separador detalhado MVSep Mega53 (aproximadamente 1,37 GB) e outros modelos ausentes são baixados no primeiro uso e ficam no cache. No Windows, separação, identificação e refinamento executam no computador; no Codespaces, executam na máquina do GitHub. Os motores não exigem API paga. CPU funciona; o modo automático exige mais processamento que as opções tradicionais.

Se a janela não abrir, execute **INICIAR_COM_DIAGNOSTICO.bat**. O log fica em `%LOCALAPPDATA%\FaixaLab\FaixaLab.log`.

## Importar uma música

1. Clique **Nova música** e cole o link YouTube ou escolha o MP3.
2. Mantenha **Automático por IA · todos os candidatos**, já selecionado.
3. Aguarde separação, verificação dos instrumentos, cifra e letra. A primeira utilização também baixa os modelos necessários. O progresso mostra trechos concluídos e atividade do processamento.
4. Ouça cada faixa com S/M; use as abas **Stems, Letra, Acordes, Formas e Tablatura**.

Não é necessário indicar previamente quais instrumentos a música contém. O mesmo conjunto de 33 categorias é avaliado em toda importação automática. O número de faixas depende do áudio e da evidência obtida. Guitarra elétrica, violão, acordeon e gaita de boca têm saídas próprias; o estágio solo/base usa a guitarra elétrica.

A identificação de timbres pode ser incerta com efeitos ou gravações incomuns. Estimativas substanciais que ainda contribuam à reconstrução ficam disponíveis com **· estimativa** no nome. Essa indicação não confirma o instrumento. Áudio não atribuído permanece em **Outros instrumentos**. A tablatura continua exigindo confirmação de guitarra e de presença de solo; uma estimativa sem confirmação não autoriza transcrever tudo como solo.

O acesso ao YouTube depende da disponibilidade do vídeo e da conexão. A qualidade da separação depende dos modelos públicos e da gravação; não há garantia de identificar corretamente qualquer instrumento em qualquer música.

## Atualizar um projeto existente

Abra **Stems → Separação de stems → Refinar instrumentos por IA** e mantenha **Detectar automaticamente**. O app usa o áudio original já salvo; não é necessário baixar o vídeo ou enviar a música novamente. Letra e correções manuais da cifra continuam preservadas. Os acordes são recalculados sobre o novo acompanhamento e a revisão de áudio invalida a tablatura anterior.

A separação inicial usa Demucs. O refinamento detalhado reaproveita sua saída de bateria e reestima os demais instrumentos a partir do original. Isso é uma decisão interna do fluxo, sem uma opção especial de manutenção de bateria.

**Escolher categorias** é uma opção avançada, de até 18 categorias, para solicitar explicitamente saídas do separador. O modo automático não precisa dessa seleção. As opções tradicionais de 4/6/7 faixas também continuam disponíveis. **Separar tudo novamente** refaz a etapa inicial conforme as opções salvas.

## O que mudou no motor

- A identificação nas faixas antigas deixou de bloquear a extração. O modo automático executa todos os 33 heads disponíveis no catálogo antes de verificar suas saídas.
- A presença é avaliada em contextos locais. Um instrumento que toca brevemente não é eliminado apenas pela média de volume da música inteira. A normalização vale somente para a entrada do classificador; os WAVs mantêm o ganho original.
- Famílias e filhos sobrepostos são resolvidos após a inferência; estimativas praticamente idênticas não entram duas vezes na mixagem.
- Saídas muito baixas recebem uma segunda conferência no áudio original menos as outras estimativas aceitas. Saídas de timbre incerto são incorporadas em ordem de energia quando reduzem o erro de reconstrução. Nenhum resíduo é injetado em guitarra ou outro instrumento; o restante fica em Outros.
- O catálogo inclui também clarinete, trombone, trompa, cravo, sitar, marimba, pandeiro/pandeirola, tímpanos e carrilhão de vento.

MVSep Mega53 e EfficientAT são redes pré-treinadas. Esta versão altera a coordenação e verificação da inferência; não treina uma rede nova com músicas enviadas pelo usuário. Os ensaios, inclusive fontes ainda não resolvidas corretamente, estão em **VALIDACAO.md**.

## Guitarra solo → tablatura

1. Abra um projeto com **Guitarra solo** e **Guitarra base** separadas. Se ainda houver uma guitarra única, use **Separação de stems → Separar guitarra solo e guitarra base**.
2. Clique no nome **Guitarra solo**, na sua onda ou em **S** nessa faixa. A aba Tablatura abre e inicia a primeira análise automaticamente. O padrão é **Somente solos · detecção automática**. Tablaturas das versões anteriores são identificadas como antigas e recalculadas ao abrir a aba.
3. A tablatura acompanha o transporte comum. Clique numa nota para ouvir o trecho; dê dois cliques para corrigir corda, casa ou tempo, ou excluir uma nota indevida.
4. A análise mostra botões **Solo 1**, **Solo 2** etc. com os intervalos encontrados. Clique para navegar. Sem solo confirmado, a tablatura automática fica vazia. Para transcrever deliberadamente toda a guitarra, escolha **Faixa inteira · inclui acompanhamento** e recalcule.
5. Escolha a região do braço e **Melodia solo** ou **Até duas notas simultâneas**. **Recalcular tablatura** reaproveita o stem salvo e o cache de detecção da mesma revisão de áudio. A opção **Manter minhas correções** preserva notas editadas e exclusões, inclusive notas que você tenha colocado fora dos trechos detectados; desmarque para recomeçar.
6. Exporte **TXT** ou **MIDI**. Os eventos mantêm os tempos da música original, incluindo o silêncio antes de um solo no meio. O ZIP do projeto inclui esses dois arquivos quando houver uma tablatura da versão atual.

As posições usam E A D G B e, casas 0–24, e procuram continuidade de digitação. São sugestões para executar as alturas detectadas; o áudio não identifica de forma unívoca a corda usada pelo músico. A grade visual é 4/4, com tempo baseado no BPM estimado, sem quantizar os eventos exportados. Parênteses indicam uma nota sustentada de um bloco anterior. Transposição e capotraste da cifra não alteram a tablatura nem o áudio.

A detecção e a transcrição são locais. Primeiro, o EfficientAT classifica o timbre real em contextos sobrepostos e compara a evidência de guitarra com acordeon, gaita de boca e outros instrumentos. Observações isoladas precisam de evidência mais forte. O nome guitar_lead.wav não autoriza a tablatura por si só. Depois, o classificador MERT examina janelas de cinco segundos, com passo de um segundo, na mistura e na guitarra solo. Confirmação em ambas as vistas e energia relativa solo/base delimitam os candidatos; a evidência de notas ainda polifônica rejeita um acompanhamento predominantemente de acordes antes de forçar uma/duas vozes. A presença de voz não aplica um veto absoluto. Os mesmos parâmetros são usados para todas as músicas. A detecção da tablatura não altera os WAVs separados. A separação detalhada ocorre automaticamente na importação padrão; projetos existentes podem receber o mesmo processamento pelo botão Refinar instrumentos por IA.

Vazamentos, arpejos de acompanhamento, solos em acordes, distorção e arranjos densos ainda podem provocar falsos positivos ou omissões. Um trecho dominado por três ou mais notas simultâneas pode ser recusado como acompanhamento; **Faixa inteira** permite estudar esse material. Bends, slides, ligados e vibrato não recebem articulações automáticas. Os testes não estabelecem precisão garantida sobre qualquer repertório; resultados e casos de reserva estão em **VALIDACAO.md**.

## Dicionário de violão/guitarra

- Abra **Dicionário de acordes** pelo botão de grade na barra lateral, inclusive sem uma música aberta.
- Pesquise cifras, escolha fundamental e família, e compare as posições no braço. A base contém **1.047 cifras, 3.078 posições e 44 famílias**: tríades, suspensos, sextas, sétimas, nonas, décimas primeiras, décimas terceiras, alterações e inversões. Aceita aliases como C7M, Cm7M e C6/9.
- Cada posição mostra cordas abafadas/soltas, pestanas, dedos e notas efetivamente tocadas. As notas permitidas, os intervalos obrigatórios e o baixo são conferidos. Extensões podem omitir notas opcionais.
- **Usar esta posição nos diagramas** grava sua preferência local para aquela cifra.
- Dois cliques em um acorde da música abrem o dicionário para corrigi-lo. Selecione a cifra e **Aplicar ao trecho**; **Marcar trecho sem acorde** usa N. A correção continua preservada em análises posteriores.

O dicionário valida a relação entre a cifra e sua digitação. A identificação da harmonia no áudio continua usando BTC/CQT; adicionar diagramas ao catálogo não retreina o reconhecedor. Erros de escolha do acorde na música podem ser corrigidos pelo dicionário.

## Usar os refinamentos

- **Nova música → 7 faixas**: voz, bateria, baixo, guitarra solo, guitarra base, piano e outros. A etapa solo/base usa um modelo próprio e está identificada como experimental. A primeira utilização baixa aproximadamente 337 MB adicionais.
- **Stems → Separação de stems → Separar guitarra solo e guitarra base**: divide a guitarra de um projeto existente, sem refazer todas as outras faixas.
- **Nova música → Equilibrado**: opção padrão, com um modelo de separação. **Estúdio** mantém o ensemble de quatro modelos e, com seis faixas, mais uma passada para guitarra e piano; exige mais tempo de CPU.
- **Stems → Separação de stems → Separar novamente as faixas**: escolha Equilibrado, Estúdio ou Mais rápido e reprocese uma música existente; também recalcula sua cifra. Preserva a letra e os intervalos de acordes corrigidos manualmente. Usa o áudio original já salvo, sem baixar o vídeo ou enviar o arquivo novamente.
- **Stems → Separação de stems → Cifra → Estável → Recalcular somente a cifra**: usa o acompanhamento já salvo e preserva correções manuais. Se houver uma separação em andamento e faixas anteriores disponíveis, **Parar separação e recalcular a cifra** encerra o worker antes de iniciar a análise.
- A faixa azul identifica uma cifra calculada anteriormente. Ela só é substituída depois de uma nova análise bem-sucedida.
- A linha do tempo abre oito compassos por janela, com tempo e número do compasso identificados. Use +/− para zoom, a barra horizontal para navegar ou a pequena visão geral sob a régua. A janela acompanha a reprodução; uma navegação manual suspende o acompanhamento por quatro segundos.
- O progresso mostra cada trecho realmente processado, inclusive a segunda passada do modo Estúdio, tempo decorrido e tempo desde a última saída do worker. A ausência de uma nova saída não é classificada automaticamente como travamento.
- Clique duas vezes em um acorde para corrigi-lo pelo dicionário. A correção fixa aquele intervalo nas próximas análises.

O modo Estúdio combina modelos especializados para voz, bateria e baixo. Quando há seis faixas, extrai guitarra e piano em uma etapa adicional sobre o acompanhamento após retirar as três primeiras fontes. A soma das faixas preserva o áudio original, sem normalizar cada instrumento com um volume diferente.

A cifra combina dois Transformers BTC pré-treinados, evidência espectral do acompanhamento e informação grave. A entrada do reconhecedor é o acompanhamento instrumental: a voz e a guitarra solo são excluídas. O modo estável usa decodificação semi-Markov, com duração mínima de duas batidas limitada ao intervalo 0,6–1,2 s, maior peso do reconhecedor de tríades e penalidade para extensões. O resíduo Outros tem peso reduzido quando existem faixas dedicadas de acompanhamento; o baixo contribui com menor ganho. Analisa contexto em janelas sobrepostas e ajusta transições próximas a ataques reais, respeitando a duração mínima. **Detalhada** mantém mudanças mais rápidas, extensões e inversões automáticas com evidência no baixo. Correções manuais prevalecem sobre essas restrições.

Reconhecimento e separação continuam sendo estimativas. A divisão solo/base ainda apresentou vazamentos e regressões em parte dos testes controlados; não tem qualidade garantida equivalente à do Moises. O piano do modelo de seis fontes, acordes ambíguos e arranjos densos também podem apresentar classificações incorretas. Os resultados medidos desta atualização estão em **VALIDACAO.md**.

## Recursos

- MP3, WAV, FLAC, M4A, AAC, OGG, OPUS, MP4 e WebM de até 1 GB; importação de um vídeo por link HTTPS do YouTube.
- Quatro faixas: voz, bateria, baixo e outros. Seis faixas: acrescenta guitarra e piano. Sete faixas: divide a guitarra em solo e base.
- Mixer com volume por faixa, mute, solo, comparação com original, seek, velocidade e repetição A/B.
- Acordes maiores, menores, 6, m6, 7, maj7, m7, mMaj7, dim, aug, dim7, m7b5, sus2 e sus4; inversões com baixo indicado quando sustentadas pela análise.
- Diagramas de guitarra/violão que verificam as notas do acorde e o baixo, priorizam posições abertas ou famílias móveis E/Am e mostram pestana e dedos. Bm usa `x24432`; F#m usa `244222`. Os controles de transposição e capotraste mudam a cifra e a posição sugerida, sem transpor o áudio.
- Letra sincronizada por palavra, edição, exportação WAV/LRC/TXT/ChordPro/JSON e ZIP.
- Tablatura sincronizada do solo, edição/exclusão de notas e exportação MIDI/TXT.
- Dicionário pesquisável com posições conferidas e preferências de digitação.

## Dados e execução

O áudio é processado na máquina que executa o backend: seu computador no modo Windows, ou a instância do GitHub no modo nuvem. Não é enviado ao Moises. Internet é usada para obter vídeos e baixar modelos ainda ausentes. A importação YouTube depende da disponibilidade do vídeo e da conexão; a entrada por arquivo funciona independentemente desse acesso após baixar os modelos necessários.

Projetos: `%LOCALAPPDATA%\FaixaLab\projects`. Modelos: `%LOCALAPPDATA%\FaixaLab\models`. `FAIXALAB_DATA_DIR` permite outro diretório. Cada refinamento grava um novo conjunto de faixas antes de trocar a referência do projeto; a análise anterior não é sobrescrita durante o processamento.

O executável é um iniciador Windows x64; a interface usa pywebview/WebView2, com alternativa no navegador padrão. O backend escuta uma porta de loopback. A instalação padrão usa PyTorch CPU; GPU atua se já houver uma instalação PyTorch compatível com CUDA.

## Implementação

| Parte | Implementação |
| --- | --- |
| Iniciador/janela | Go Windows AMD64; pywebview/WebView2 |
| Backend | Python 3.12; FastAPI; um trabalho ativo |
| Estúdio, quatro faixas | Demucs `htdemucs_ft`, ensemble especializado por fonte |
| Estúdio, seis faixas | `htdemucs_ft`, depois `htdemucs_6s` no acompanhamento |
| Rápido/equilibrado | `htdemucs` ou `htdemucs_6s`, conforme o número de faixas |
| Instrumentos detalhados | MVSep Mega53 v1, heads selecionados; bateria anterior preservada |
| Identificação de timbres | EfficientAT MN10 AudioSet ONNX, frontend Kaldi/Mel conferido |
| Guitarra solo/base | HTDemucs Lead/Rhythm Dry Paint, aplicado à guitarra elétrica refinada no modo detalhado |
| Cifra | BTC 170 + BTC 25, CQT, semi-Markov no modo estável; inversões no detalhado |
| Detecção de solos | MERT Guitar Solo MJN ONNX int8, duas vistas, predominância de fontes e continuidade temporal |
| Tablatura | Basic Pitch ONNX; verificação polifônica, seleção de vozes e busca de cordas/casas |
| Dicionário | chords-db MIT, filtrado e validado; vocabulário comum ao backend e aos diagramas |
| Letra | faster-whisper 1.2.1; PyAV 15.1.0; Whisper multilíngue |
| YouTube | yt-dlp + EJS; Deno local |

BTC e seus pesos têm a origem registrada em `faixalab/models/btc_metadata.json`; a licença e as adaptações estão em `faixalab/vendor/btc`. Os checkpoints distribuídos contêm apenas tensores e valores primitivos e são carregados com `weights_only=True`. A configuração evita reutilizar caches JIT de outra execução ou instalação.

As imagens do Moises fornecidas na conversa servem de referência para o fluxo de estudo. Separação e análise usam os modelos públicos identificados acima.

## Desenvolvimento e testes

Com o ambiente preparado: `.venv\Scripts\python.exe launcher.py`. Servidor: `launcher.py --no-browser --port 8767`.

- `python -m unittest discover -s tests -v`: lógica, API, correções e linha temporal.
- `node tests/test_guitar.js`: 174 diagramas verificados contra as notas esperadas.
- `python tests/integration_run.py`: MP3 → seis faixas → cifra → Whisper → streaming e ZIP, com motores reais.
- `python tests/cancel_and_analyze.py`: após a integração, cancela um worker real e recalcula a cifra preservando faixas, letra e correção manual.
- `node tests/test_studio_ui.cjs`: regressão de DOM, incluindo música de 203 s; requer `npm install --no-save jsdom@26.1.0`.
- `python tests/solo_regressions.py`: teste de 203 s; `--guitar-recording caminho.wav` acrescenta um trecho real, sem medir acerto na ausência de anotações.
- `python tests/solo_integration.py`: ONNX real, notas conhecidas, edição, exclusão, preservação e exportação MIDI/TXT/ZIP.
- `python tests/instrument_job_integration.py --controls pasta --checkpoint modelo.ckpt --output resultado.json`: tarefa real da API, novas fontes, bateria idêntica, anotações e ZIP.
- `python tests/instrument_audio_regressions.py --controls pasta --checkpoint modelo.ckpt --output resultado.json`: referências reais de acordeon/violão, erro de timbre e SI-SDR.
- `python tests/solo_job_integration.py gravacao.wav --output resultado.json`: tarefa real da API no modo automático, cache, seções e tempos originais no MIDI.
- `python tests/solo_presence_regressions.py --recordings pasta --export pasta_exportada --output resultado.json`: detector e transcritor reais sobre gravações externas e controles de ausência de solo. `--held-out` usa os exemplos de reserva. Os áudios dos datasets e do usuário não são distribuídos.
- `python tests/chord_regressions.py`: comparação com a versão 0.1 em áudio sintetizado conhecido; não é um benchmark de repertório real.

Para compilar o iniciador: `GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -H=windowsgui" -o FaixaLab.exe desktop_launcher.go`.
