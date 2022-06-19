---
title: "GraphQL APIに負荷テストを実施するアイディア"
emoji: "🦗"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["GraphQL", "Locust"]
published: true
---
GraphQL APIに負荷テストを実施するアイディアを紹介します。ご参考になれば幸いです。

# 使用ツール
- docker compose
- Locust

上記ツールの概要や使用方法は公式ドキュメントやその他の二次情報をご参考にしてください。
```sample project
.
├── docker-compose.yml
├── locustfile.py
└── payloads
    ├── hoge
    │   └── hoge.json
    └── fuga
        └── fuga.json
```

# docker compose
```yml:docker-compose.yml
version: '3'

services:
  locust-master:
    image: locustio/locust
    ports:
     - "8089:8089"
    volumes:
      - ./:/mnt/locust
    command: -f /mnt/locust/locustfile.py --master -H ${LOCUST_HOST} --tags ${LOCUST_SCENARIO}
  
  locust-worker:
    image: locustio/locust
    volumes:
      - ./:/mnt/locust
    command: -f /mnt/locust/locustfile.py --worker --master-host locust-master
```
本記事ではLocustをdocker composeで実行します。[別記事](https://zenn.dev/zenn/articles/bbe899f8a78cfd)で紹介しますが、負荷テストの実行状況をGrafanaで可視化したく、サービス連携しやすいdocker composeを利用する方法を選択しました。

docker composeを利用した実行方法については以下をご参考にしてください。
https://docs.locust.io/en/stable/running-in-docker.html

変数化箇所について説明します。

まず、docker composeでは環境変数を使用することができます。本記事ではdocker composeコマンドの実行時に以下のように環境変数を渡すようにしました。
```shell
export LOCUST_HOST="https://api.example.com" LOCUST_SCENARIO="hoge";docker compose up
```
変数化目的は以下になります。
```shell
# 負荷試験用の各種ファイルをstaging,production環境で共通化したいため、負荷テストを実施するエンドポイントを変数化します
${LOCUST_HOST}　

# ユーザーが訪問するページによってGraphQLのリクエストが異なると想定しページ毎にtask（シナリオ）を記述しています
# taskを@tagでデコレートし、Locust実行時にtagsオプションにtag名を指定することでtaskを切り替えます
${LOCUST_SCENARIO}
```
なお本記事ではLocust GUIを使用して負荷テストを開始します。ワーカー数はdocker composeコマンド実行時に以下のように調整します。
```shell
export LOCUST_HOST="https://api.example.com";docker compose up --scale locust-worker=10
```

# Locust
```python:locustfile.py

from locust import HttpUser, task, between, events, constant, tag
from pathlib import Path
import json

# GraphQL APIのパス例
path = "/graphql/query"

# HTTPリクエストボディが記述されたJSONファイルのパス（query_path)を受け取り、HTTPリクエストをループする関数
def loop_http_request(self, query_path):
  for query in query_path.glob('*.json'):
    with open(query,"r") as request_body:
      payload = json.load(request_body)
    
      response = self.client.post(
          path,
          headers = {},
          json = payload,
          name = str(query)
      )

      '''
      #HttpUserのリクエスト内容をログに出力する(debug用)
      body = response.request.body
      print("=====requestInfo=====")
      print("client:", response.request.headers['client'])
      print("content-length", response.request.headers['Content-Length'])
      print("body:", body.decode("utf-8"))
      print("response_time:", response.elapsed.total_seconds())
      print("=====================")
      '''

# HTTPリクエスト失敗をフックするイベント処理
@events.request_failure.add_listener
def request_handler(name, response_time, response_length, exception):
  print(f"operationName:{name}, response_time:{response_time}, response_length:{response_length}, exception:{exception}")

class LoadTest(HttpUser):
    # constant(n):同時接続数 RPSではない
    # wait_time = constant(2)

    # between(n, n): sleepする間隔を指定
    wait_time = between(5, 20)

    # 認証を行う. on_startはセッションの開始時に必ず実行される.
    def on_start(self):
      #requestのbodyを記載
      payload = [] 
      
      response = self.client.post(
        path,
        headers = {
          "Content-Type": "application/json"
        },
        json = payload,
        name = "createSession"
      )

      response_body = response.json()
　　　 
      # レスポンスボディから認証情報を取得する. Keyは例.
      auth = response_body[0]['data']['Session']['auth']

      # デフォルトのリクエストヘッダーをセットする。classの関数内に headers = {} と記載したときは以下のヘッダーが付与される。
      self.client.headers.update(
        {
          "auth-info": auth['authInfo'],
          "Content-Type": "application/json"
        }
      )

    @tag('hoge')
    @task
    def query_on_top_page(self):
      query_path = Path('/mnt/locust/payloads/hoge/')
      loop_http_request(self, query_path)

    @tag('fuga')
    @task
    def queries_on_category_page(self):
      query_path = Path('/mnt/locust/payloads/fuga/')
      loop_http_request(self, query_path)
```
コード内のコメントでは説明不足と感じることを補足します。

## loop_http_request
GraphQLのoperation nameとHTTPリクエストが1:1であると想定し、HTTPリクエストのボディを記述したjsonファイルを必要なだけ用意します。ページに対応したディレクトリ下（/mnt/locust/payloads/hoge/）にjsonファイルを配置してループを回します。このようにすることで、WebブラウザやiOS/AndroidアプリからAPIに送信するHTTPリクエストに近い負荷を発生させます。

## wait_time
クラスで定義しているwait_timeによって、LocustのHTTPクライアントはランダムな秒数スリープします。下限と上限の時間を調整することで実際のユーザーの行動に近づけることができると思います。LocustのHTTPクライアント数＝同時接続数としたい場合は、constantを使用します。

# 参考にしたブログ
以下のブログを参考にしています。
https://loadforge.com/directory/graphql_test_example