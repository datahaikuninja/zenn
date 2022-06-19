---
title: "GraphQL APIにLocustで負荷テストを実施し、operation name別にレスポンスタイムをGrafanaでグラフ化する"
emoji: "📈"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["GraphQL", "Locust", "Grafana"]
published: false
---
Grafanaを使ってLocustの負荷試験の状況をグラフ化する試みの紹介です。
# 背景
Locust GUIは負荷テスト中のリクエスト統計を表示する機能と、トータルRPSとレスポンスタイムをグラフ化する機能を持っています。

これらの機能で十分なこともありますが、REST APIのURI別やGraphQL APIのoperation name別のRPSとレスポンスタイムが知りたいとなると、Locustが提供してくれる機能では実現できません。

本記事ではGraphQL APIのoperation name別のレスポンスタイムをグラフ化するために、外部ツールを利用した実現方法を紹介します。

先に完成イメージを見たい方は、目次からoperation name別のレスポンスタイムへ進んでください。

なお本記事で紹介する、operation name別のレスポンスタイムのグラフ化は以下が前提となります。
>GraphQLのoperation nameとHTTPリクエストが1:1であると想定[^1]

[^1]:https://zenn.dev/zenn/articles/22eac1a6d4ad8b

# 使用ツール
- docker compose
- Locust
- locust-exporter
- Prometheus
- Grafana

```:sample-project
.
├── docker-compose.yml
├── grafana
│   ├── dashboard.yml
│   ├── dashboards
│   │   └── locust_dashboard.json
│   └── datasource.yml
├── locustfile.py
├── payloads
│   ├── hoge
│   │   └── hoge.json
│   └── fuga
│       └── fuga.json
├── prometheus
│   └── prometheus.yml
└── sample-dashboards
    └── grafana
        └── locust_dashboards.json
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
  
  locust-metrics-exporter:
    image: containersol/locust_exporter
    ports:
      - "9646:9646"
    environment:
      - LOCUST_EXPORTER_URI=http://locust-master:8089
    depends_on:
      - locust-master
  
  prometheus:
    image: prom/prometheus
    container_name: prometheus
    ports:
      - "9090:9090"
    command: 
      - --config.file=/etc/prometheus/prometheus.yml
    volumes:
      - ./prometheus:/etc/prometheus
  
  grafana:
    image: grafana/grafana
    container_name: grafana
    ports:
      - "3000:3000"
    volumes:
      - ./grafana/dashboards:/var/lib/grafana/dashboards/
      - ./grafana/datasource.yml:/etc/grafana/provisioning/datasources/datasource.yml
      - ./grafana/dashboard.yml:/etc/grafana/provisioning/dashboards/dashboard.yml
    environment:
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Admin
      - GF_AUTH_ANONYMOUS_ENABLED=true
    restart: always
```
# Locust
locustfileは以下記事をご参考にしてください。
https://zenn.dev/zenn/articles/22eac1a6d4ad8b

# locust-exporter
https://github.com/ContainerSolutions/locust_exporter
LocustからPrometheusへメトリクスデータをエクスポートするサービスです。シンプルなツールですから、本記事ではdocker-compose.ymlに記載している内容以上の説明はありません。

# Prometheus
```yaml:prometheus/prometheus.yml
# https://prometheus.io/docs/prometheus/latest/configuration/configuration/

global:
  scrape_interval: 10s

scrape_configs:
- job_name: prometheus
  static_configs:
  - targets:
    - locust-metrics-exporter:9646
```
`prometheus/prometheus.yml`のコメントのURLに記載のテンプレートをベースにカスタマイズします。本記事ではアラート等の設定は不要なため、最低限以下の設定のみ記述しています。
- scrape_interval: スクレイプ間隔
- static_configs: スクレイプ対象

# Grafana
```yaml:grafana/datasource.yml
# https://grafana.com/docs/grafana/latest/administration/provisioning/#example-data-source-config-file

# config file version
apiVersion: 1

datasources:
  # <string, required> name of the datasource. Required
  - name: Prometheus
    # <string, required> datasource type. Required
    type: prometheus
    # <string, required> access mode. proxy or direct (Server or Browser in the UI). Required
    access: proxy
    url: http://prometheus:9090
    editable: true
```
`grafana/datasource.yml`にはGrafanaのデータソース設定を記述します。本記事では最低限の内容しか記述していませんが、ファイル内コメントのURLに記載のテンプレートをベースに必要に応じて書き足してください。

```yml:grafana/dashboard.yml
# https://grafana.com/docs/grafana/latest/administration/provisioning/#dashboards

apiVersion: 1

providers:
  # <string> an unique provider name. Required
  - name: 'locust'
    # <int> Org id. Default to 1
    orgId: 1
    # <string> name of the dashboard folder.
    folder: ''
    # <string> folder UID. will be automatically generated if not specified
    folderUid: ''
    # <string> provider type. Default to 'file'
    type: file
    # <bool> disable dashboard deletion
    disableDeletion: true
    # <int> how often Grafana will scan for changed dashboards
    updateIntervalSeconds: 10
    # <bool> allow updating provisioned dashboards from the UI
    allowUiUpdates: false
    options:
      # <string, required> path to dashboard files on disk. Required when using the 'file' type
      path: /var/lib/grafana/dashboards
      # <bool> use folder names from filesystem to create folders in Grafana
      foldersFromFilesStructure: true
```
`grafana/dashboard.yml`でダッシュボードを作成・管理します。ダッシュボードのウィジェットは`grafana/dashboards/locust_dashboard.json`に記述します。

`grafana/dashboards/locust_dashboard.json`は、以下をベースにカスタマイズするのがよいと思います。
https://github.com/ContainerSolutions/locust_exporter/blob/main/locust_dashboard.json

カスタマイズせずにそのまま使用すると以下のイメージのダッシュボードが出来上がります。
https://github.com/ContainerSolutions/locust_exporter/blob/main/locust_dashboard.png

# operation name別のレスポンスタイム
以下のようにoperation name別のレスポンスタイムを表示することができます。X軸の下にoperation nameが表示されますが、本記事ではカットしています。
![](/images/bbe899f8a78cfd/image-1.png)

`grafana/dashboards/locust_dashboard.json`の記載内容のうち、上記のグラフの設定箇所を抜粋しておきます。テンプレートをベースにカスタマイズしています。
:::details grafana/dashboards/locust_dashboard.jsonの抜粋
```json
    {
      "aliasColors": {},
      "bars": false,
      "dashLength": 10,
      "dashes": false,
      "datasource": "Prometheus",
      "decimals": 0,
      "editable": true,
      "error": false,
      "fill": 0,
      "fillGradient": 0,
      "gridPos": {
        "h": 24,
        "w": 24,
        "x": 0,
        "y": 32
      },
      "hiddenSeries": false,
      "id": 6,
      "legend": {
        "avg": false,
        "current": false,
        "max": false,
        "min": false,
        "show": true,
        "total": false,
        "values": false
      },
      "lines": true,
      "linewidth": 2,
      "links": [],
      "nullPointMode": "connected",
      "options": {
        "dataLinks": []
      },
      "paceLength": 10,
      "percentage": false,
      "pointradius": 2,
      "points": true,
      "renderer": "flot",
      "seriesOverrides": [{
        "alias": "AVG MAX",
        "yaxis": 2
      }],
      "spaceLength": 10,
      "stack": false,
      "steppedLine": false,
      "targets": [
        {
          "expr": "avg(locust_requests_median_response_time{method=~\".+\"}) by (method, name)",
          "intervalFactor": 2,
          "legendFormat": "AVG MEDIAN {{name}}",
          "refId": "D",
          "step": 2
        }
      ],
      "thresholds": [],
      "timeFrom": null,
      "timeRegions": [],
      "timeShift": null,
      "title": "Response Times per query",
      "tooltip": {
        "msResolution": false,
        "shared": true,
        "sort": 0,
        "value_type": "individual"
      },
      "type": "graph",
      "xaxis": {
        "buckets": null,
        "mode": "time",
        "name": null,
        "show": true,
        "values": []
      },
      "yaxes": [{
          "decimals": 0,
          "format": "ms",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": null,
          "show": true
        },
        {
          "decimals": 0,
          "format": "ms",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": null,
          "show": true
        }
      ],
      "yaxis": {
        "align": false,
        "alignLevel": null
      }
    }
```
:::
# operation name別にレスポンスタイムがグラフ化される仕組み
[GraphQL APIに負荷テストを実施するアイディア](https://zenn.dev/zenn/articles/22eac1a6d4ad8b)の記事で紹介したlocustfile内に次の記述があります。
```python:locusutfile.py
name = str(query) #name毎にLocustGUIでリクエスト統計情報を確認可能
```
このqueryには、LocustのHTTPクライアントのリクエストボディが記載されたJSONファイル名が入っています。以下をご覧ください。JSONファイル名はoperation nameを判別可能な名前にしておきます。
```python:locusutfile.py
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
```
このname情報はPrometheusにも渡っており、locust_dashboard.jsonの以下の記述によって、operation name別にレスポンスタイムのグラフ表示が可能になっています。
```json:grafana/dashboards/locust_dashboard.json
"legendFormat": "AVG MEDIAN {{name}}"
```
# 参考ブログ
本記事は以下のブログ記事をヒントに着想を得ました。
https://mzqvis6akmakplpmcjx3.hatenablog.com/entry/2021/06/30/191740

また、作業手順は以下のブログ記事を参考にしています。
https://medium.com/devopsturkiye/locust-real-time-monitoring-with-grafana-66654bb4b32
本記事を参考にしていただくときも、参考ブログ記事にならって、docker-compose.ymlを少しずつ書き足しながら進めていくことをおすすめします。