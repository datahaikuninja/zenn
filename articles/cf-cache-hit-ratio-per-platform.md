---
title: "CloudFrontの標準ログからプラットフォーム毎のキャッシュHit率を求めるAthenaのクエリ"
emoji: "🔍"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["AWS", "Athena", "CloudFront"]
published: true
---
# 概要
CloudFrontの標準ログからプラットフォーム毎のキャッシュHit率を求めるAthenaのクエリを紹介します。

# 背景
CloudFrontのキャッシュHit率は次の方法で確認することが可能です。
1. [CloudFrontコンソールのキャッシュ統計レポート](https://docs.aws.amazon.com/ja_jp/AmazonCloudFront/latest/DeveloperGuide/cache-statistics.html)
2. [CloudFrontディストリビューションの追加のメトリクス](https://docs.aws.amazon.com/ja_jp/AmazonCloudFront/latest/DeveloperGuide/viewing-cloudfront-metrics.html#monitoring-console.distributions-additional)

レポートと追加メトリクスから知ることが可能なのはディストリビューション単位のキャッシュHit率です。

通常、ディストリビューション単位のキャッシュHit率を確認できれば十分ですが、プラットフォーム毎のキャッシュHit率を知りたいという要望があったので、CloudFrontの標準ログからプラットフォーム毎のキャッシュHit率を求めるAthenaのクエリを考えました。

この要望の背景について軽く触れておきます。記事の本筋からは脱線するので興味がある人だけお読みください。
:::details 要望の背景

ログ解析対象のCloudFrontはGraphQLのレスポンスをキャッシュしています。POSTリクエストの結果をキャッシュしないCloudFrontにどうやってGraphQLのレスポンスをキャッシュさせるのか、という疑問を持たれると思いますが、別の機会に記事にします。

GraphQLのクエリ(operationName)はプラットフォーム毎に異なります。つまり、Web（PC/SP)とネイティブアプリ（iOS/Android）は、GraphQLサーバーに対してプラットフォーム毎に異なるクエリを発行します。

プラットフォーム毎にキャッシュしていいクエリが異なるので、CloudFrontが受け取ったリクエストの結果をキャッシュできるかどうか、Lambda@Edgeでチェックしています。

そうやって、プラットフォーム毎にキャッシュしていいクエリ（リクエスト）の結果をCloudFrontにキャッシュしますが、レポートと追加メトリクスから知ることができるキャッシュHit率はディストリビューション単位なので、全プラットフォームのキャッシュHit率が総合されたものになっています。

CloudFrontに限らず、オリジンの負荷をオフロードするというCDNのメリットを享受するためにはキャッシュHit率は高い方が良いですが、キャッシュHit率を高くしようというときに、キャッシュHit率をプラットフォーム毎に可視化することができると、キャッシュ可能なクエリを増やすべきプラットフォームが分かります。

仮に一部のプラットフォームのキャッシュHit率の低下が原因でディストリビューション単位のキャッシュHit率が低下した場合、プラットフォーム毎のキャッシュHit率が分かるとキャッシュHit率低下の原因調査と改善が効率化されます。

また、プラットフォーム毎に開発チームが別れている組織では、各チームが担当するプラットフォームのキャッシュHit率が分からないとキャッシュHit率を向上させるモチベーションも湧きづらいということもあります。

こういうわけで、プラットフォーム毎のキャッシュHit率を知りたいという要望になります。
:::

# Athenaのクエリ
CloudFrontログをクエリするためのテーブルは作成済みとします。テーブルの作成方法は[公式ドキュメント](https://docs.aws.amazon.com/ja_jp/athena/latest/ug/cloudfront-logs.html)に従います。

例えば、次のクエリで過去1日間のiPhoneから送信されたリクエストのキャッシュHit率を求めます。
```sql
/*
クエリの汎用性のために期間指定にprestoの日付関数を使用する。
また、一時テーブルを作成しておくと割算の記述が短くなる。
*/
WITH last_day AS (SELECT * FROM polaris WHERE "date" BETWEEN DATE_ADD('day', -1, CURRENT_DATE) AND CURRENT_DATE)

SELECT
/*
prestoで整数同士の割算をすると値が0になってしまうので、割る数のデータ型をDOUBLEにCASTする。
*/
 CAST(SUM(CASE WHEN cs_user_agent LIKE '%iPhone%' AND x_edge_result_type = 'Hit' THEN 1 ELSE 0 END) AS DOUBLE) /
 SUM(CASE WHEN cs_user_agent LIKE '%iPhone%' THEN 1 ELSE 0 END) 
FROM last_day;
```
## 解説
LambdaからAthenaのクエリを定期的に発行する可能性を考慮して相対的に期間指定できるようにしたかったので日付関数を使っています。[^1]

Athenaエンジンバージョン2は、[Presto 0.217](https://prestodb.io/docs/0.217/index.html)に基づくので[^2]、ドキュメントから使えそうな関数を探します。

>date_add(unit, value, timestamp) → [same as input][^3]
>Adds an interval value of type unit to timestamp. Subtraction can be performed by using a negative value.
>timestampにunit型の区間値を加算する。負の値を用いれば減算も可能である。

>current_date -> date[^3]
>Returns the current date as of the start of the query.
>問い合わせ開始時点の日付を返す。

DATE_ADDは、第一引数に指定した単位で、第二引数に指定したUnit値を第三引数のtimestampに追加します。
CURRENT_DATEはクエリ発行時点の日付を返します。
よって、クエリ発行日の1日前からクエリ発行日までの期間を指定できます。[^4]

Prestoで整数同士の割算をすると想定に反して値が0になる事象について検索すると、割算の片方を小数点数に型変換する方法が見つかります。[^5][^6]

他のプラットフォームのキャッシュHit率を求めるときは、`LIKE '%デバイスを識別するUserAgent%'`、期間指定を変更したければDATE_ADDとCURRENT_DATEの箇所を変更します。

# 補足
CloudFrontのログがS3に保存されるとき、ELBやWAFのように年月日時間でオブジェクトのパスが分かれないです。
ELBやWAFのログならテーブル作成時にログのパスを指定してAthenaでスキャンするログを絞ることができるのですが、CloudFrontのログだと同じことをやるのは厳しいです。
ログを溜めているS3のライフサイクルポリシーにもよりますが、CloudFrontのログは溜まりがちだと思うので、Athenaでスキャンしたデータ量に対する課金に気をつけたいです。

実は上記のクエリをスケジュール発行するLambdaとEventBridgeを作って、結果をNewRelicとかに送って可視化しようと思っていました。
しかし、一時テーブルを作成するときにCloudFrontのログをフルスキャンしていたので、Athenaの費用が高くなりそうで諦めました。

[^1]:Prestoの日付関数の使用方法についてはこちらを参考にしました。https://qiita.com/AkiQ/items/b865614a6ed7c86953c6
[^2]:https://docs.aws.amazon.com/ja_jp/athena/latest/ug/presto-functions.html
[^3]:https://prestodb.io/docs/0.217/functions/datetime.html
[^4]:この書き方はこちらのブログを参考にしました。https://tech.dely.jp/entry/2020/12/02/090024
[^5]:こちらのブログを参考にしました。https://box406.hatenablog.com/entry/2017/03/24/162252
[^6]:Athenaのデータ型についてはこちらの公式ドキュメントを参照。https://docs.aws.amazon.com/ja_jp/athena/latest/ug/data-types.html