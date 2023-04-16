---
title: "EventBridge API Destinationを使ってNew Relic Metric APIを呼び出す"
emoji: "💥"
type: "tech"
topics: ["AWS", "EventBridge", "NewRelic",]
published: false
---
## 概要
Amazon EventBridgeには外部APIとの連携を楽にするAPI Destinationという機能があります。
この機能を使用することにより、外部API呼び出しを非同期的に扱うことができます。[^1]

この記事では、New RelicのMetric APIをAPI Destinationで呼び出してみました。
Metric APIとは、メトリクスデータの取り組み口のようなものです。送信元のアプリケーションから、任意のデータをカスタムメトリクスとして送信することができます。例えば、エージェントで収集できないデータをNew Relicでモニタリングしたいときに役に立ちます。

New RelicのMetric APIを活用している方、これから使ってみる方だけでなく、外部APIの呼び出しを非同期的に扱いたい方の参考になれば幸いです。

## アーキテクチャ
下図のように非常にシンプルです。EventBridgeが送信元からイベントを受信し、イベントの中身をNew RelicのMetric APIにPOSTします。
![](/images/call-nr-metric-api-with-eventbridge/eventbridge_api_destination.png)

### EventBridge API Destinationを使うメリット
例えば、ECサイトの売上をNew Relicで可視化することで、ビジネスKPIの監視を行ったり、システムパフォーマンス指標と売上の関連性について洞察を得たいとします。

購入情報をNew Relicに送信したいとき、アプリケーションからNew RelicのMetric APIを直接呼び出す素朴な方法が考えられます。
![](/images/call-nr-metric-api-with-eventbridge/naive.png)

この方法は以下の点で好ましくありません。
- 外部APIのレスポンスを待機する(同期的)
- 外部APIのレスポンスに応じたエラーハンドリングやリトライ処理が必要

外部API呼び出しの結果によってアプリケーションのパフォーマンスが悪化する可能性がありますし、メインロジックとは無関係のエラーハンドリングやリトライ処理を実装するコストがかかります。

素朴な方法のデメリットを解決する方法の一つはバッチ処理です。定期的にデータベースから購入情報を取得して整形し、New Relicへデータを送信します。
![](/images/call-nr-metric-api-with-eventbridge/batch.png)

この方法は悪くありませんが、以下の点が気になります。
- バッチ処理を実行するLambdaやECSの運用管理が必要
- リアルタイム性がない

LambdaやECSの料金はほとんど気になりませんが、リソースを運用管理するコストはできればなくしたいです。
また、バッチ処理の実行間隔によりますが、New Relicに取り込まれる購入情報は常に実行間隔の分古くなります。リアルタイム性は不可欠というほどではありませんが、まさに今売上にどのような影響があるのか見たいことがあります。

EventBridgeのAPI Destinationを使ったイベント駆動処理にすれば、バッチ処理の気になる点を解消できます。また、送信元はSDKでPutEventsをするだけで済むので実装がシンプルです。[^2]

従来、EventBridgeからイベントを受け取って外部APIを呼び出すためにLambda関数を書く必要がありましたが、API Destinationを使えばその必要もありません。

## サンプル
以下では、AWS CLIでPutEventsを実行し、EventBridgeからNew RelicのMetric APIにECサイトの購入情報をカスタムメトリクスとして送信してみます。（実際の購入情報ではありません）

試してみるには、New Relicのアカウントと[licenseキー](https://docs.newrelic.com/jp/docs/apis/intro-apis/new-relic-api-keys/#license-key)を準備してください。

### 構築
マネジメントコンソールから以下のAWSリソースを作成します。
- カスタムバス
- ルール
- ターゲット
- APIの送信先（API Destination）
- API接続
- IAMロール

検証においては、EventBridgeのデフォルトバスを使っても構いませんが、アプリケーション独自のイベントはカスタムバスへ送信する方が望ましいです。[^3]

#### カスタムバスの作成
以下のドキュメントに従いカスタムバスを作成します。
https://docs.aws.amazon.com/ja_jp/eventbridge/latest/userguide/eb-create-event-bus.html

#### ルールの作成
以下のドキュメントを参考にしながらルールを作成します。
https://docs.aws.amazon.com/ja_jp/eventbridge/latest/userguide/eb-create-rule.html

以下は、この記事の手順におけるポイントです。引用は直前のドキュメントからです。

1. ルールを作成するとき、イベントバスは、カスタムバスを選択してください。

> [Event bus] (イベントバス) で、このルールに関連付けるイベントバスを選択します。このルールをアカウントからのイベントと一致させるには、AWS のデフォルトのイベントバスを選択します。アカウントの AWS のサービスで発生したイベントは、常にアカウントのデフォルトのイベントバスに移動します。

2. イベントソースは「その他」を選択します。

> [Event source] (イベントソース) で、[AWS events or EventBridge partner events] ( イベントまたは EventBridge パートナーイベント) を選択します。

3. イベントパターンは、カスタムイベントパターンを選び、エディタに以下のJSONを貼り付けます。
```JSON
{
  "source": ["test"],
  "detail-type": ["purchaseEvent"]
}
```

4. ターゲットタイプは「EventBridge API の宛先」を選択します。
> [Target type] (ターゲットタイプ) で、以下のいずれかのオプションを選択します。

5. 「新しいAPIの送信先」を選択し、画面に従って作成します。
> 新しい API 送信先を作成するには、[Create a new API destination] (新しい API 送信先を作成する) を選択します。次に、送信先について次の詳細を指定します。

6. API送信先エンドポイントは、`https://metric-api.newrelic.com/metric/v1` を入力し、HTTPメソッドはPOSTを選択します。
7. 「新しい接続」を選び、画面に従って新規のAPI接続を作成します。
8. 送信先タイプはパートナーを選び、パートナー送信先にNew Relicを選ぶと、認証タイプにAPIキー指定されます。
9. APIキー名は`Api-Key:New Relicアカウントのlicenseキー`とします。
10. 実行ロールを新規に作成します。ここで作成されるIAMロールには`"events:InvokeApiDestination"`をAllowするPolicyが付与されます。
11. 追加設定において、ターゲット入力を「入力トランスフォーマー」に変更します。
12. 入力パスと入力トランフォーマーに以下のJSONを貼り付けます。

入力パス
```JSON
{
  "deviceType": "$.detail.deviceType",
  "purchaseAmount": "$.detail.purchaseAmount",
  "timestamp": "$.detail.timestamp"
}
```

入力トランスフォーマー
```JSON
[
    {
        "metrics": [
            {
                "name": "purchaseData",
                "type": "gauge",
                "value": <purchaseAmount>,
                "timestamp": <timestamp>,
                "attributes": {
                    "deviceType": "<deviceType>"
                }
            }
        ]
    }
]
```
送信元から受け取ったデータを入力トランスフォーマーでNew Relic Metric APIで有効なJSON形式に変換を行います。[^4]
送信元でPutEventsするときにNew RelicのMetric APIで有効なJSON形式でデータを作成しても構いません。この記事では、送信元からは変数のみを送信し、EventBridgeで固定値を付け足してメトリクスデータを作成しました。

### イベントの送信
CloudShellを立ち上げて構築手順で作成したカスタムバスにデモデータをPutします。[^5]
```bash
aws events put-events \
  --entries '[{"Source": "test", "DetailType": "purchaseEvent", "EventBusName": "作成したカスタムバス名", "Detail": "{ \"timestamp\": `有効なunixtime`, \"purchaseAmount\": 20000, \"deviceType\": \"iOS\" }"}]'
```

New Relicにメトリクスデータが取り込まれたことを確認します。
```SQL
FROM Metric SELECT sum(purchaseData) FACET deviceType TIMESERIES AUTO
```

### トラブルシューティング
New Relicにメトリクスデータが取り込まれていない場合、いくつか確認する箇所があります。

1. EventBridgeルールのモニタリング
構築手順で作成したルールの呼び出し状況を確認します。`Invocations`, `TriggeredRule`メトリクスが記録されていれば、PutEventsは成功しています。

https://docs.aws.amazon.com/ja_jp/eventbridge/latest/userguide/eb-monitoring.html

2. New Relicのメトリクスデータ取り込み状況
New Relicに取り込む際にエラーが発生していないか、以下のドキュメントを参考にして確認します。

https://docs.newrelic.com/jp/docs/data-apis/ingest-apis/metric-api/troubleshoot-nrintegrationerror-events/

3. SQSのデッドレターキュー
New Relicで取り込みエラーが確認できない場合、EventBridgeルールからターゲットに向けたイベント配信の最中に何らかの問題が発生した可能性があります。以下のドキュメントを参考にSQSのデッドレターキューを設定して、トラブルシューティングを行います。

https://docs.datadoghq.com/ja/logs/guide/sending-events-and-logs-to-datadog-with-amazon-eventbridge-api-destinations/#%E3%83%88%E3%83%A9%E3%83%96%E3%83%AB%E3%82%B7%E3%83%A5%E3%83%BC%E3%83%86%E3%82%A3%E3%83%B3%E3%82%B0

https://docs.aws.amazon.com/ja_jp/eventbridge/latest/userguide/eb-debug-event-delivery.html

## おわりに
呼び出し元から外部APIを直接呼び出すのではなくEventBridgeに行わせることで、API呼び出しを非同期的に扱うことができますし、リトライ等の考慮ポイントを減らすこともできます。外部APIと連携する要件をもつシステムを設計するとき、EventBridge API Destinationを使えないか検討してみてはいかがでしょうか。

[^1]: API Destinationの解説は以下の発表資料を参照。
https://speakerdeck.com/_kensh/the-art-of-eventbridge?slide=34

[^2]: 送信元と外部APIの間にEventBridgeを挟むと、外部API呼び出しを非同期的に扱うことができ、リトライや認証もEventBridgeに任せることができます。ただ、EventBridgeもまた送信元から見れば外部APIになります。よって、PutEventsのエラーハンドリングを考慮したり、PutEventsを非同期的に実行するための工夫はしなければいけません。

[^3]: デフォルトバスとカスタムバスの使い分け方は以下の発表資料を参照
https://pages.awscloud.com/rs/112-TZM-766/images/20200122_BlackBelt_EventBridge.pdf

[^4]: 入力トランスフォーマーの使用方法は以下のドキュメントを参照
https://docs.aws.amazon.com/ja_jp/eventbridge/latest/userguide/eb-transform-target-input.html
New Relic Metric APIに送信するデータタイプ、必須のヘッダー、有効なJSON形式は以下のドキュメントを参照
https://docs.aws.amazon.com/ja_jp/eventbridge/latest/userguide/eb-transform-target-input.html

[^5]: AWS CLIを使用したカスタムイベントの送信方法は以下のドキュメントを参照
https://docs.aws.amazon.com/ja_jp/eventbridge/latest/userguide/eb-putevents.html#eb-send-events-aws-cli