---
title: "コスト削減のためにCloudWatchログに長年溜まったログデータをS3にエクスポートするスクリプトを書いた"
emoji: "💵"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["AWS", "CloudWatch", "Python"]
published: true
publication_name: "vega_c"
---
:::message
2023/02/06 追記

S3にエクスポートされたログのキーにエクスポートタスクIDとログストリーム名が自動的に付与されてしまうため、ログデータのパーティション化は困難でした。Athenaではファイルの場所の指定にワイルドカードやglobパターンを使用することができません。

Athenaのテーブル定義で以下のように記述することでパーティション可能な想定でしたが、実際には異なりました。

```
LOCATION 's3://{bucket_name}/{prefix}'
TBLPROPERTIES
(
 "projection.enabled" = "true",
 "projection.day.type" = "date",
 "projection.day.range" = "2020/01/01/00,NOW",
 "projection.day.format" = "yyyy/MM/dd/",
 "projection.day.interval" = "1",
 "projection.day.interval.unit" = "DAYS",
 "storage.location.template" = 's3://{bucket_name}/{prefix}/${day}/**'
)
```

パーティション化は困難ですが、本記事で紹介するスクリプトは年月日でキーを分けてエクスポート可能なため、ファイルの場所（LOCATION）に目当ての年月日を指定することで、余計にデータスキャンせずに済みます。
:::
https://docs.aws.amazon.com/ja_jp/athena/latest/ug/tables-location-format.html

# 概要
コスト削減のためにCloudWatchログのログデータをS3へエクスポートするスクリプトを書いたので、書いた経緯やスクリプトを実行してみて分かった豆知識を書き残したものです。

# 意外と高いCloudWatch
円安により、システムそのものやリクエスト数に大きな変化はないにも関わらずAWS料金が高くなってしまって困るので、コスト削減をやることになりました。
コスト削減をやるときは料金が高いところから手をつけていくのでCostExplorerや料金明細を見るわけですが、CloudWatchは意外と利用料金の上位にいます。

CloudWatchの費用が高くなる理由と解決方法については以下のナレッジセンターのブログが参考になります。
https://aws.amazon.com/jp/premiumsupport/knowledge-center/cloudwatch-understand-and-reduce-charges/

個人的には、CloudWatchメトリクスのAPIコール, CloudWatchログのデータ取り込み, 分析（インサイトの使用）がCloudWatchの料金が高くなる要因だと思っています。
CloudWatchの料金はこちらです。
https://aws.amazon.com/jp/cloudwatch/pricing/

この記事はCloudWatchログのログデータ保管にかかる料金を減らすために書いたスクリプトの話なのですが、データ保管コストはそこまで高くならないので、まずはこの三つの料金状況を確認した方がよいです。

ナレッジセンターのブログにも書かれていますが、サードパーティのモニタリングツールが頻繁にCloudWatchメトリクスAPIを呼び出すことでコストが高くなる場合があるので、モニタリングツールと統合するAWSサービスを必要なものに絞ったり、ポーリング間隔を見直すなど対処しましょう。

ログのデータ取り込み、分析は気をつけないと料金が膨れ上がります。東京リージョンのログデータの取り込み料金は$0.76 per GBです。仮に1日に10GBの新しいログを取り込むロググループが一つあると、このロググループだけで月に$235.6ものデータ取り込み料金がかかります。アクセスログのようなログ量がリクエスト数に比例するログを全くフィルタせずにCloudWatchログに放り込んでいると、リクエスト数のスパイクでデータ取り込み料金も跳ね上がるので、ログ送信元側でフィルタしたり[^1]、取り込むログを制限したりしましょう。

取り込んだままの大量のログに対して気軽にインサイトのクエリを発行していると後から料金にびっくりするかもしれません。東京リージョンのログ分析料金は$0.0076 per GBです。1日に10GBの新しいログを取り込むロググループを対象に1ヶ月間のログを分析すると、一回の分析で$2.356かかります。期待した結果が得られず、クエリを何度も発行すると毎回費用がかかります。
またCloudWatchダッシュボードにクエリを追加していると、ダッシュボードを読み込むごとに分析費用がかかるので注意が必要です[^2]。

CloudWatchログのデータ保管料金の削減の前に、メトリクスAPI、ログデータ取り込み、ログ分析の料金を確認しましょう。

# ログデータをエクスポートする理由
CloudWatchログのデータ保管費用はそこまで高くならないと書きましたがS3よりは高いです。
東京リージョンのログデータ保管は$0.033 per GBです。仮に1TBのデータを保管する場合、CloudWatchログは$33かかり、東京リージョンのS3標準に保管する場合は$25です。
S3の料金はこちらです。
https://aws.amazon.com/jp/s3/pricing/

長期間保管するならS3の方が安いですし、ログを集計分析するのにかかる料金についてもログインサイトよりAthenaの方が安いです。（Athenaは$5.00 per TB）
Athenaの料金はこちらです。
https://aws.amazon.com/jp/athena/pricing/

この記事で紹介したいエクスポートスクリプトを書いた理由は、監査要件で長期間保管しなければならないアプリケーションログが2年以上前からロググルーブに溜まり続けて7TBを超えていて、毎月$200以上のデータ保管料金がかかっていたので、より長期保管に適したS3にログを移してコストを浮かせたかったからです。
このアプリケーションログは昨年からCloudWatchログではなくNew Relic LogsとS3へ転送しており[^3]、New Relic Logsへ転送する前のログをS3に移してしまえば、ロググループに保管期間を設定して古いログを一掃することができます。

# エクスポートスクリプト
スクリプトはこちらに置きました。
https://github.com/datahaikuninja/cloudwatch-logs-exporter

スクリプトの特徴と使い方はREADMEに書いています。この記事でREADMEとスクリプトの内容を補足できればと思います。

~~## Athenaでパーティショニングできるようにした理由
監査対応等でログを集計分析しなければならなくなったときのためです。仮に年毎に階層を分けてログをエクスポートしたら、2022年12月1日のログを調べるために2022年の全ログをAthenaでスキャンする羽目になります。Athenaはスキャンしたデータ量に応じて料金がかかるのでお金がもったいないですし、スキャンに時間がかかりすぎます。そもそもパーティショニングできるようにする必要がないなら、マネジメントコンソールでエクスポートタスクを作成してしまった方が早いし楽です。[^4]~~

## create_export_taskのInvalidParameterExceptionを無視している理由
スクリプトに引き渡した期間にマッチするログが存在しないとこの例外が発生しましたが、存在しないならその期間はスキップしてほしかったのでその旨を意味するメッセージを出力してスクリプトを継続させるようにしました。
スクリプトを動かして分かった豆知識ですが、この例外は条件の期間にマッチするログがロググループ内に一行も存在しない場合に発生し、指定したlogStreamNamePrefixのログストリーム内に条件の期間にマッチするログがない場合は`aws-logs-write-test`という名前のファイルがS3に作成されるようでした。
例えば、2020年1月1日から1月31日の期間のログをエクスポートするためにこのスクリプトを実行すると、1月1日から16日はInvalidParameterExceptionが発生したためこれをキャッチして処理を継続し、その後17日から31日までのログをエクスポートするエクスポートタスクが作成されたが、S3を確認すると各日付の階層の下にログはなく`aws-logs-write-test`という名前のファイルがあるだけ、といった状況でした。
ちなみに、指定したlogStreamNamePrefixのログストリーム内に条件の期間にマッチするログが見つかると、エクスポートされたログファイルは次のような階層構造を持ちます。
```
s3://example-bucket/api/2020/08/01/{exportTaskName}/{logStreamName}/000000.gz
```
なお、この豆知識はAWSサポートに確認していないので、あくまでスクリプトの動作から推測したに過ぎません。

## エクスポートタスクが完了するまでsleepして待っている理由
CloudWatchログのサービスクォータが理由です。
>リソースクォータ – CloudWatch Logs サービスクォータでは、リージョンあたりのアカウントごとに 1 つの実行中または保留中のエクスポートタスクのみ許可されます。このクォータは変更できません。許可されたクォータ内で操作していることを確認してください。
https://aws.amazon.com/jp/premiumsupport/knowledge-center/cloudwatch-get-logs-to-export-to-s3/

このクォータがあるためエクスポートタスクの完了を待っています。エクスポートタスクを複数作成することができないので、ロググループ内のログデータ量によってはエクスポートにかなりの時間がかかります。

## エクスポートしたらCloudWatchログのログデータは消えるのか
消えないです。CLIやSDK、マネジメントコンソールから作成したエクスポートタスクが仮に失敗しても、ログは残っているのでリトライできます。

# おまけ
ログの保持期間が設定されていないロググループを調べるために作ったコマンドと保持期間をちょっと楽に設定するために作ったコマンドも置いておきます。CloudShellで実行してあげると楽できるかもしれません。
https://gist.github.com/datahaikuninja/eef0d50b9551ee0ce60de06b62e41772#file-aws-cli-logs-sh

[^1]: EC2インスタンスとオンプレミスサーバーで使用可能なCloudWatchエージェント側でフィルタできるようになったのは割と最近のようです。 https://dev.classmethod.jp/articles/cwagent-log-stream-filters/
ECSではFireLensによるカスタムログルーティングで正規表現を使用したログのフィルタリングがサポートされています。https://docs.aws.amazon.com/ja_jp/AmazonECS/latest/developerguide/firelens-filtering-logs.html
[^2]: これは罠だと思う。「ダッシュボードに追加したクエリは、ダッシュボードをロードおよび更新するたびに再実行されます。」 https://docs.aws.amazon.com/ja_jp/AmazonCloudWatch/latest/logs/CWL_ExportQueryResults.html
[^3]: アプリケーションログをNew Relic LogsとS3へ送るアイディアはこちらの発表が参考になります。　https://speakerdeck.com/onohiroshi1/roguji-pan-wocloudwatchlogkaranewrelic-logs-plus-s3nibian-etara-li-bian-xing-moshang-gatutekosutomoxia-gatutahua
[^4]: 大量のログを一括エクスポートするとどのくらい時間がかかるのかについて、ドキュメントには「ログデータは、エクスポートできるようになるまで最大 12時間かかる場合があります。」と記載があります。https://docs.aws.amazon.com/ja_jp/AmazonCloudWatch/latest/logs/S3Export.html
スクリプトを実行してみた実感としては、1ヶ月のログをエクスポートするのに6~8時間かかることがあるので本当に12時間で終わるのか？と思いました。期間とlogStreamNamePrefixの条件でログを探索させているから1ヶ月分のログのエクスポートに6~8時間かかっているのかもしれませんが。
