---
title: "Lambda(Python)でAurora PostgreSQL論理レプリケーションを監視して、Slackにアラート通知する"
emoji: "🤖"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["AWS", "Lambda", "Python", "Slack"]
published: false
---
# 概要
Lambda(Python)で以下の処理（関数）を書いたので、備忘録として記事にします。
```
a. pg8000を使用して、LambdaからPostgreSQLデータベースへ接続し、論理レプリケーションの統計情報を取得する

b. boto3を使用して、LambdaからCloudWatch Logsにクエリを発行してエラーログを抽出する

c. slack_sdkを使用して、LambdaからSlackにメッセージを投稿し、スレッドにスニペット（ファイル）を追加する
```
Lambda(Python)あるいは、boto3でこれらの処理を書きたい人には参考になるところがあるかもしれません。

# 背景
## やりたかったこと
Aurora Postgresの論理レプリケーションが遅延or停止する障害を検出し、CloudWatch Logsに出力されているPostgreSQLログからエラーを抽出してSlackに送りたい。

## 困っていたこと
論理レプリケーションしているAurora PostgreSQLクラスターがあり、レプリケーション先のクラスターのレプリケーションが遅延or停止する障害を検出する必要がある。
レプリケーション元はパブリッシャー、レプリケーション先はサブスクライバーと呼ばれる。

サブスクライバー側のPostgreSQLログはCloudWatch Logsに出力されていて、'ERROR'という文字列の出現を監視することで障害を検出をしていたが、レプリケーションとは無関係のエラーによる誤報アラートが多く、監視の改善が必要だった。

そこで、サブスクライバー側に対して`SELECT * FROM pg_stat_subscription;`クエリを定期的に発行し、フィールドの値を監視することで監視の精度を高めることにした。('a'の処理)

Aurora PostgreSQLの論理レプリケーションが遅延or停止する障害が発生したらSlackにアラートを通知し、この通知のメッセージのスレッドにPostgreSQLのエラーログを貼り付けようと考えた。（'b', 'c'の処理）

# 処理の説明
コード内のAWSアカウントIDとAWSリソース名は全てサンプルです。

## pg8000を使用して、LambdaからPostgreSQLデータベースへ接続し、論理レプリケーションの統計情報を取得する
```python
from datetime import datetime, timedelta, timezone
import boto3
import botostubs
import pg8000.native

errorType = ''
ssmParameterPathDB = f'{SSM_PARAMETER_PATH}sample-cluster/'

resp = ssm.get_parameters_by_path(
    Path=ssmParameterPathDB,
    Recursive=True,
    WithDecryption=True
)

params = {}
for param in resp['Parameters']:
    params[param['Name']] = param['Value']

hostname = params[f'{ssmParameterPathDB}hostname']
username = params[f'{ssmParameterPathDB}username']
dbname = params[f'{ssmParameterPathDB}dbname']
password = params[f'{ssmParameterPathDB}password']

try:
    con = pg8000.native.Connection(
        host=hostname,
        user=username,
        port='5432',
        database=dbname,
        password=password
    )

    sqlResp = con.run(
        'SELECT last_msg_send_time, last_msg_receipt_time FROM pg_stat_subscription;')

    if sqlResp[0][0] is None or sqlResp[0][1] is None:
      　'''エラータイプ：論理レプリケーションが停止
          無効なサブスクリプションやクラッシュしたサブスクリプションはテーブルの行が0になる
          配列の要素がNoneの場合、論理レプリケーションが停止していると判断する
        '''
        errorType = 'pg_stat_subscription table has no row. Logical replication has stopped.'
        raise Exception
    else:
        lastMessageSendTime = sqlResp[0][0]
        lastMessageReceiptTime = sqlResp[0][1]

    currentTime = datetime.now(timezone.utc)

    if ((currentTime - lastMessageSendTime) / timedelta(seconds=1)) > float(300):
      　'''エラータイプ：パブリッシャー側の遅延
        last_msg_send_time: 発行元から受信した最後のメッセージの送信時刻
        current_time - last_msg_send_time の時間差をtimedeltaを使用して秒単位で計算する（結果はfloat型）
        300秒以上の時間差があったら、パブリッシャーからメッセージ送信が遅延していると判断する
        '''
        errorType = 'Logical Replication is delaying at the publisher.'
        raise Exception

    elif ((lastMessageSendTime - lastMessageReceiptTime) / timedelta(seconds=1)) > float(300):
      　'''エラータイプ：サブスクライバー側の遅延
        last_msg_receipt_time: 発行者から最後に受信したメッセージの受信時刻
        last_msg_send_time: 発行元から受信した最後のメッセージの送信時刻
        last_msg_receipt_time - last_msg_send_time の時間差をtimedeltaを使用して秒単位で計算する（結果はfloat型）
        300秒以上の時間差があったら、サブスクライバーのメッセージ受信が遅延していると判断する
        なお、普段は負の数になるのが正常。正の数になると遅延が発生していることになる。
        '''
        errorType = 'Logical Replication is delaying at the subscriber.'
        raise Exception

    else:
        print('currentTime - lastMessageSendTime:',
              ((currentTime - lastMessageSendTime) / timedelta(seconds=1)))
        print('lastMessageSendTime - lastMessageReceiptTime:',
              ((lastMessageSendTime - lastMessageReceiptTime) / timedelta(seconds=1)))
        print('Logical replication is operational.')

    except pg8000.native.DatabaseError as pg8000DatabaseError:
        '''pg8000がサーバーから受け取るエラー
        con = pg8000.native.Connection()の失敗をキャッチすることを想定した例外
        '''
        print(pg8000DatabaseError)
        errorType = 'Lambda function(pg8000) received an error from the database.'
        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    except pg8000.native.InterfaceError as pg8000InterfaceError:
        '''pg8000内部で発生するエラー
        con.run()の失敗をDatabaseErrorかInterfaceErrorのいずれかでキャッチする想定
        '''
        print(pg8000InterfaceError)
        errorType = 'Lambda function(pg8000) raised an internal error.'

        # 後述する'b'の処理
        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )

        # 後述する'c'の処理
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    except pg8000.native.Error as pg8000GeneralError:
        '''pg8000の汎用的なエラー
        DatabaseError or InterfaceErrorのいずれにも該当しない例外
        '''
        print(pg8000GeneralError)
        errorType = 'Generic exception on pg8000(lambda function).'
        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    except:
        # pg_stat_subscriptionテーブルから取得した統計情報をもとに判断された例外
        print('Lambda handler raise bare Exception.')

        # 想定外のエラーが発生した場合はここでerror_typeに値を代入する
        if not errorType:
            errorType = 'Unexpected exceptions'

        '''あまり一般的ではないと思うが、以下のように書くアイディアもあった
        if 'error_type' not in locals():
            error_type = 'Unexpected exceptions'
        '''

        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    finally:
        con.close()
```

## boto3を使用して、LambdaからCloudWatch Logsに出力されたログからエラーを抽出する
```python
import json
import time
from datetime import datetime, timedelta, timezone
import boto3
import botostubs

logs: botostubs.CloudWatchLogs = boto3.client('logs')

CW_LOGS_GROUP = 'arn:aws:logs:ap-northeast-1:012345678910:log-group:/aws/rds/cluster/sample-db-cluster/postgresql'
CW_LOGS_INSIGHT_QUERY = 'fields @timestamp, @message | filter @message like "ERROR" | sort @timestamp asc | limit 5'

'''こうなります
targetLogGroup = CW_LOGS_GROUP
queryStr = CW_LOGS_INSIGHT_QUERY
'''


def issue_query_to_cw_logs(targetLogGroup, queryStr):
    '''CloudWatch Logs Insightsを使用して、エラーログを抽出する関数
    対象ロググループに対してクエリ発行時点から過去30分間に記録されたエラーログのうち最大5件を取得する
    Slackへエラーログを送信するために、エラーログを格納した変数を戻り値として返す

    Args:
        targetLogGroup: Logs Insightsのクエリを発行する対象ロググループ
        queryStr: Logs Insightsのクエリ文
    '''
    issuedQueryInfo = logs.start_query(
        logGroupName=targetLogGroup,
        startTime=int((datetime.today() - timedelta(minutes=30)).timestamp()),
        endTime=int(datetime.now().timestamp()),
        queryString=queryStr
    )

    issuedQueryId = issuedQueryInfo['queryId']
    issuedQueryResults = None

    while issuedQueryResults is None or issuedQueryResults['status'] == 'Running':
        time.sleep(1)
        issuedQueryResults = logs.get_query_results(
            queryId=issuedQueryId
        )

    neededQueryResults = []
    for result in issuedQueryResults['results']:
      　'''
      　なお、logs.start_queryのstartTimeを、timedelta(minutes=5)のようにして、クエリ対象期間の開始時刻を現在時刻に近づけるとissuedQueryResults['results']が空のリストになってしまった
      　同内容のクエリをマネジメントコンソールから発行すると期待した結果が得られるので原因は不明
      　boto3から実行する場合は、現在時刻の30分ほど前の時刻を開始時刻に指定して回避している
      　'''

        # クエリ結果の二次元配列からfieldの値が@ptrの要素(一次元配列)を取り除き、必要な要素だけ抽出した二次元配列を作成する
        extracted = [
            element for element in result if not element['field'] == '@ptr']
        neededQueryResults.append(extracted)

    results = json.dumps(neededQueryResults, indent=4)
    '''多少読みやすくするために以下のフォーマットに変換する
    [
        [
            {
                "field": "@timestamp",
                "value": "timestamp"
            },
            {
                "filed": "@message",
                "value": "error message"
            }
        ],
        [
            {
                "field": "@timestamp",
                "value": "timestamp"
            },
            {
                "filed": "@message",
                "value": "error message"
            }
        ],
    ]
    '''
    return results
```

## slack_sdkを使用して、LambdaからSlackにメッセージを投稿し、スレッドにスニペット（ファイル）を追加する。
```python
import boto3
import botostubs
from slack_sdk import WebClient

ssm: botostubs.SSM = boto3.client('ssm')

SSM_PARAMETER_PATH = '/monitoring-logical-replication/'


def notification_to_slack(errorType, slackSnipet):
    '''Slackへアラートを通知する関数
    二通のメッセージをSlackへ送信する。
    まず、アラートのタイトルと後続の対応を指示する内容を通常のメッセージとして送信する。
    次に、CloudWatch Logs Insightsで取得したエラーログを最初のメッセージのスレッドに追加する形で送信する。

    Args:
        errorType: どのようなアラートか分かるタイトル。Lambda関数のハンドラー内で決定する
        slackSnipet: スレッドに追加するエラーログファイルの内容。issue_query_to_cw_logs関数で作成する
    '''
    ssmParameterPathSlack = f'{SSM_PARAMETER_PATH}slack/'

    resp = ssm.get_parameters_by_path(
        Path=ssmParameterPathSlack,
        Recursive=True,
        WithDecryption=True
    )

    params = {}
    for param in resp['Parameters']:
        params[param['Name']] = param['Value']
    slackBotToken = params[f'{ssmParameterPathSlack}bot-token']
    slackChannelId = params[f'{ssmParameterPathSlack}channel-id']
    slackClient = WebClient(token=slackBotToken)

    respPostMessage = slackClient.chat_postMessage(
        channel=slackChannelId,
        text="Information",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*logical replication alert* :alert:"
                },
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Alert description* \n{errorType}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*How to check logical replication status* \nPlease log in to sample-cluster and \
                            execute the following SQL \n```SELECT * FROM pg_stat_subscription;```"
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*PostgreSQL error log location* \n```arn:aws:logs:ap-northeast-1:012345678910:log-group:\
                            /aws/rds/cluster/sample-cluster/postgresql```"
                    }
                ]
            }
        ]
    )

    slackThread = respPostMessage.get('ts')
    slackClient.files_upload_v2(
        channel=slackChannelId,
        content=slackSnipet,
        filename='errors.json',
        snippet_type='json',
        initial_comment='Up to 5 errors in the last 30 minutes',
        thread_ts=slackThread
    )
```

# Lambda関数全体
'a'の処理も含むLambda関数全体も載せておきます。
::: details Lambda関数全体
```python
import json
import time
from datetime import datetime, timedelta, timezone
import boto3
import botostubs
import pg8000.native
from slack_sdk import WebClient

ssm: botostubs.SSM = boto3.client('ssm')
logs: botostubs.CloudWatchLogs = boto3.client('logs')

SSM_PARAMETER_PATH = '/monitoring-logical-replication/'
CW_LOGS_GROUP = 'arn:aws:logs:ap-northeast-1:012345678910:log-group:/aws/rds/cluster/sample-db-cluster/postgresql'
CW_LOGS_INSIGHT_QUERY = 'fields @timestamp, @message | filter @message like "ERROR" | sort @timestamp asc | limit 5'


def issue_query_to_cw_logs(targetLogGroup, queryStr):
    issuedQueryInfo = logs.start_query(
        logGroupName=targetLogGroup,
        startTime=int((datetime.today() - timedelta(minutes=30)).timestamp()),
        endTime=int(datetime.now().timestamp()),
        queryString=queryStr
    )

    issuedQueryId = issuedQueryInfo['queryId']
    issuedQueryResults = None

    while issuedQueryResults is None or issuedQueryResults['status'] == 'Running':
        time.sleep(1)
        issuedQueryResults = logs.get_query_results(
            queryId=issuedQueryId
        )

    neededQueryResults = []
    for result in issuedQueryResults['results']:
        extracted = [
            element for element in result if not element['field'] == '@ptr']
        neededQueryResults.append(extracted)

    results = json.dumps(neededQueryResults, indent=4)

    return results


def notification_to_slack(errorType, slackSnipet):
    ssmParameterPathSlack = f'{SSM_PARAMETER_PATH}slack/'

    resp = ssm.get_parameters_by_path(
        Path=ssmParameterPathSlack,
        Recursive=True,
        WithDecryption=True
    )

    params = {}
    for param in resp['Parameters']:
        params[param['Name']] = param['Value']

    slackBotToken = params[f'{ssmParameterPathSlack}bot-token']
    slackChannelId = params[f'{ssmParameterPathSlack}channel-id']
    slackClient = WebClient(token=slackBotToken)

    response_to_chat_post_message = slackClient.chat_postMessage(
        channel=slackChannelId,
        text="Information",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*logical replication alert* :alert:"
                },
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Alert description* \n{errorType}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*How to check logical replication status* \nPlease log in to sample-cluster and \
                            execute the following SQL \n```SELECT * FROM pg_stat_subscription;```"
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*PostgreSQL error log location* \n```arn:aws:logs:ap-northeast-1:012345678910:log-group:\
                            /aws/rds/cluster/sample-cluster/postgresql```"
                    }
                ]
            }
        ]
    )

    slackThread = response_to_chat_post_message.get('ts')

    slackClient.files_upload_v2(
        channel=slackChannelId,
        content=slackSnipet,
        filename='errors.json',
        snippet_type='json',
        initial_comment='Up to 5 errors in the last 30 minutes',
        thread_ts=slackThread
    )


def lambda_handler(event, context):
    errorType = ''
    ssmParameterPathDB = f'{SSM_PARAMETER_PATH}sample-cluster/'

    resp = ssm.get_parameters_by_path(
        Path=ssmParameterPathDB,
        Recursive=True,
        WithDecryption=True
    )

    params = {}
    for param in resp['Parameters']:
        params[param['Name']] = param['Value']

    hostname = params[f'{ssmParameterPathDB}hostname']
    username = params[f'{ssmParameterPathDB}username']
    dbname = params[f'{ssmParameterPathDB}dbname']
    password = params[f'{ssmParameterPathDB}password']

    try:
        con = pg8000.native.Connection(
            host=hostname,
            user=username,
            port='5432',
            database=dbname,
            password=password
        )

        sqlResp = con.run(
            'SELECT last_msg_send_time, last_msg_receipt_time FROM pg_stat_subscription;')

        if sqlResp[0][0] is None or sqlResp[0][1] is None:
            errorType = 'pg_stat_subscription table has no row. Logical replication has stopped.'
            raise Exception
        else:
            lastMessageSendTime = sqlResp[0][0]
            lastMessageReceiptTime = sqlResp[0][1]

        currentTime = datetime.now(timezone.utc)

        if ((currentTime - lastMessageSendTime) / timedelta(seconds=1)) > float(300):
            errorType = 'Logical Replication is delaying at the publisher.'
            raise Exception

        elif ((lastMessageSendTime - lastMessageReceiptTime) / timedelta(seconds=1)) > float(300):
            errorType = 'Logical Replication is delaying at the subscriber.'
            raise Exception

        else:
            print('currentTime - lastMessageSendTime:',
                  ((currentTime - lastMessageSendTime) / timedelta(seconds=1)))
            print('lastMessageSendTime - lastMessageReceiptTime:',
                  ((lastMessageSendTime - lastMessageReceiptTime) / timedelta(seconds=1)))
            print('Logical replication is operational.')

    except pg8000.native.DatabaseError as pg8000DatabaseError:
        print(pg8000DatabaseError)
        errorType = 'Lambda function(pg8000) received an error from the database.'
        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    except pg8000.native.InterfaceError as pg8000InterfaceError:
        print(pg8000InterfaceError)
        errorType = 'Lambda function(pg8000) raised an internal error.'
        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    except pg8000.native.Error as pg8000GeneralError:
        print(pg8000GeneralError)
        errorType = 'Generic exception on pg8000(lambda function).'
        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    except:
        print('Lambda handler raise bare Exception.')
        if not errorType:
            errorType = 'Unexpected exceptions'
        cwLogsInsightQueryResult = issue_query_to_cw_logs(
            CW_LOGS_GROUP,
            CW_LOGS_INSIGHT_QUERY
        )
        notification_to_slack(
            errorType,
            cwLogsInsightQueryResult
        )

    finally:
        con.close()

```
:::

# 参考記事
## pg8000
公式のREADMEで十分です。
https://github.com/tlocke/pg8000

論理レプリケーションの監視に利用している`pg_stat_subscription`テーブルの各フィールドの見方、監視において何を指標とするのがよいかについては以下を参考にしました。
https://qatop.pythonwood.com/dba/ask/15714331/

https://zatoima.github.io/postgresql-logical-replication-monitoring.html

## boto3
logsの使い方は以下を参考にしました。
https://www.web-dev-qa-db-ja.com/ja/python/python%E3%81%A7boto3%E3%82%92%E4%BD%BF%E7%94%A8%E3%81%97%E3%81%A6cloudwatch%E3%83%AD%E3%82%B0%E3%82%92%E3%82%AF%E3%82%A8%E3%83%AA%E3%81%99%E3%82%8B%E6%96%B9%E6%B3%95/812882883/

## slack_sdk
公式ドキュメントを見ながら書きました。

Python Slack SDK全般に関しては以下のドキュメント
https://slack.dev/python-slack-sdk/
https://api.slack.com/methods

本記事で使用したメソッドは以下の二つ
https://api.slack.com/methods/chat.postMessage
https://api.slack.com/methods/files.upload

メッセージの整形については以下を読んで書きました
https://api.slack.com/reference/block-kit/blocks

ちなみに、Lambda関数からSlackにメッセージを送る際に使用しているアクセストークンはBot tokenです。tokenはいくつか種類がありますが、よくあるwebhookを使うやり方を使う場面と同じようなことがしたいなら、Bot tokenでよさそうです。

なお、`chat.postMessage`と`files.upload`を実行するために必要な権限をBot tokenに紐づける必要があります。どの権限が必要か、という情報はメソッドのドキュメントのRequired scopesに書いてあります。

アクセストークンの種類と使用における注意点は以下のドキュメントを読みました。
https://api.slack.com/authentication/token-types