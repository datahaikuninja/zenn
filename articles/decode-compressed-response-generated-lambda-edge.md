---
title: "Lambda@EdgeでHTTPレスポンスを圧縮したら発生した、Viewerのデコードエラーを解決する方法"
emoji: "🗜️"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["AWS", "CloudFront", "Lambda", "Python"]
published: true
---
## Lambda@EdgeでHTTPレスポンスを圧縮したら、Viewerのデコードエラーが発生した
オリジンリクエストトリガーのLambda関数でHTTPレスポンスを圧縮したら、Viewerのデコードエラーが発生してレスポンスボディをロードできなくなる事象が発生しました。

どのようにエラーを解決したか、備忘録的な記事を書こうと思います。
Lambda@Edgeで圧縮処理を行うのはかなり特殊なケースだと思いますが、同じ事象に困っている人の参考になれば幸いです。

なお、本記事内のコードはPythonですが、Node.jsでも参考になると思います。

結論から書くと、Lambda関数がreturnするレスポンスオブジェクトに`bodyEncoding`フィールドを追加したらViewerのデコードエラーを解決できました。

```python
{
    "status": status_code,
    "body": response_body_compressed_and_base64encoded,
    "bodyEncoding": "base64",
    "headers": {
        "content-encoding": [{
            "key": "Content-Encoding",
            "value": "compress format"
        }]
    }
}
```
CloudFrontは`"bodyEncoding": "base64"`のフィールドをもつレスポンスオブジェクトを受け取り、ボディが有効なbase64である場合はボディをbase64デコードしてから、Viewerへレスポンスを返します。ボディがbase64デコードされていて、`Content-Encoding`の値がリクエストヘッダーの`Accept-Encoding`の値に含まれていれば、Viewer側でデコードできます。

おそらくbase64デコードしているのはLambdaランタイムだと思いますが、正確なところは不明です。分かりやすさのために、CloudFrontがbase64デコードする、と書きました。

ちなみに、CloudFrontがbase64デコードする挙動はドキュメントに明記されていません。CloudFrontのドキュメントには、`bodyEncoding`フィールドの意味について、以下のように書かれています。

> `body` で指定した値のエンコード。有効なエンコードは `text` と `base64` のみです。`response` オブジェクトに `body` を含めるが、`bodyEncoding` を省略した場合、CloudFront は本文をテキストとして扱います。`bodyEncoding` を `base64` と指定したが本文が有効な `base64` でない場合、CloudFront はエラーを返します。

https://docs.aws.amazon.com/ja_jp/AmazonCloudFront/latest/DeveloperGuide/lambda-generating-http-responses-in-requests.html

AWSのサーバーレス開発に慣れている人なら、API GatewayとLambda FunctionURLのレスポンスオブジェクトにも`isBase64Encode`というフィールドがあることを知っていて、類似性に気づくかもしれませんが、普段からこれらのサービスに触れていない人は分からないと思います。

私はAWSのソリューションアーキテクトの人に教えてもらって、やっと`bodyEncoding`の意味を理解できました。

## そもそも、なぜレスポンスボディをbase64エンコードする必要があるのか
この記事を読む人にとってはbase64エンコードの必要性は自明かもしれませんが、疑問に思う人向けに書きます。

理由は、Lambda関数がreturnするオブジェクトのbody（HTTPレスポンスボディ）には、バイトデータをそのまま格納することができない仕様だからです。

Lambda関数がreturnするオブジェクトはJSONシリアライズされる、とドキュメントに書かれています。[^1]

Lambda関数から`json.dumps`でシリアル化できないオブジェクトがLambdaランタイムに返されると、Lambdaランタイムエラーを発生させます。[^2]

例えば、以下のサンプルコードで圧縮されたバイトデータをreturnすると、
```python
import json
import gzip


def lambda_handler(event, context):
    return {
        "statusCode": 200,
        "body": gzip.compress(b'{"key": "value"}')
    }
```
次のLambdaランタイムエラーが発生します。
```
{
  "errorMessage": "Unable to marshal response: 'utf-8' codec can't decode byte 0x8b in position 1: invalid start byte",
  "errorType": "Runtime.MarshalError",
  "requestId": "d89a76db-1837-427f-91ae-5ed1b8b55a96",
  "stackTrace": []
}
```

このエラーを回避するには、base64エンコードしてテキストへ変換する必要があります。
```python
import json
import gzip
import base64


def lambda_handler(event, context):
    return {
        "statusCode": 200,
        "body": base64.b64encode(gzip.compress(b'{"key": "value"}'))
    }
```

Lambda関数を呼び出すと以下のレスポンスが得られます。
```
{
  "body": "H4sIAEKsImUC/6tWyk6tVLJSUCpLzClNVaoFABtINTMQAAAA",
  "statusCode": 200
}
```

Lambdaランタイムが、圧縮されたバイトデータをJSONシリアライズできないので、テキストに変換するためにbase64エンコードする必要があるのです。

これはLambdaの仕様なので、Lambdaと連携するサービスがAPI GatewayでもCloudFrontでも変わりません。[^3]

## bodyEncodingフィールドの意味
Lambdaの仕様でバイトデータをそのまま扱えないのは仕方ないとしても、Lambda関数呼び出し元でbase64デコードしなければならないのは困る場合があります。この記事を書くきっかけになった事象を例にあげると、Lambda@EdgeでHTTPレスポンスのボディを圧縮し、base64エンコードしてテキストに変換してからCloudFrontにreturnし、それをそのままViewerに返してしまうと、レスポンスヘッダーの`Content-Encoding`の値とレスポンスボディの形式が整合しないため、デコードエラーが起きてしまいました。デコードエラーは、フロントエンドのJavaScriptが出したというより、ブラウザが出しているようでした。

このLambda関数がreturnするレスポンスボディはbase64エンコードされているとして、呼び出し元がbase64デコード処理を実装することもできますが面倒ですし、実装できないケースもあります。

Lambda関数呼び出し元のデコードエラーを回避するには、Lambda関数がボディをbase64エンコードしてLambdaランタイムに引き渡した後で、呼び出し元にレスポンスを返す前にbase64デコードする必要があります。（そうしないと、呼び出し元でハンドリングすることになります）

`bodyEncoding: base64`は、CloudFront（Lambdaランタイム）側でbase64デコードが必要であるか判定するフラグとして機能するというわけです。

## 余談
Lambdaのドキュメントの以下の文言から、`json.dumps`でJSONシリアライズできない型を含むオブジェクトをreturnするとエラーになると考えたのですが、すこし違うようです。
>ハンドラーから json.dumps でシリアル化できないオブジェクトが返された場合、ランタイムはエラーを返します。

すこし違う、というのは、REPL等の標準的なpython実行環境とLambdaランタイムとでは`json.dumps`の挙動が違うという意味です。

REPLで`json.dumps`にバイト列リテラルを渡すとJSONシリアライズエラーが発生します。
```python
>>> json.dumps(b'{"key": "value"}')
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/__init__.py", line 231, in dumps
    return _default_encoder.encode(obj)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/encoder.py", line 200, in encode
    chunks = self.iterencode(o, _one_shot=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/encoder.py", line 258, in iterencode
    return _iterencode(o, 0)
           ^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/encoder.py", line 180, in default
    raise TypeError(f'Object of type {o.__class__.__name__} '
TypeError: Object of type bytes is not JSON serializable
>>>
```

バイト列リテラルを圧縮して`json.dumps`に渡す場合も当然JSONシリアライズエラーが発生します。
```python
>>> json.dumps(gzip.compress(b'{"key": "value"}'))
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/__init__.py", line 231, in dumps
    return _default_encoder.encode(obj)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/encoder.py", line 200, in encode
    chunks = self.iterencode(o, _one_shot=True)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/encoder.py", line 258, in iterencode
    return _iterencode(o, 0)
           ^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.11/3.11.4_1/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/encoder.py", line 180, in default
    raise TypeError(f'Object of type {o.__class__.__name__} '
TypeError: Object of type bytes is not JSON serializable
>>>
```

私はLambdaのドキュメントを文字通りに理解して、LambdaランタイムもREPLと同様の挙動をするだろうと思ったのですが違いました。Lambda関数からバイト列リテラルを含むオブジェクトをreturnしても、Lambdaランタイムはエラーを発生させません。

以下のLambda関数コードをテストすると正常終了します。
```python
import json
import gzip


def lambda_handler(event, context):
    return {
        "statusCode": 200,
        "body": b'{"key": "value"}'
    }
```

レスポンスを確認すると、なぜかJSONシリアライズできています。
```
{
  "body": "{\"key\": \"value\"}",
  "statusCode": 200
}
```

しかし、上に書いたように、圧縮したバイトデータ含むオブジェクトをreturnしたときはLambdaランタイムエラーが発生していました。REPLとLambdaランタイムで`json.dumps`の挙動が異なります。

圧縮したバイトデータを含むオブジェクトをreturnしたときのLambdaランタイムエラーをよくみてみると、utf-8デコードできないと言っていました。
> "errorMessage": "Unable to marshal response: 'utf-8' codec can't decode byte 0x8b in position 1: invalid start byte",

推測になりますが、Lambdaランタイムはutf-8文字列として解釈できるバイト列を特別扱いしているんだろうと思います。Lambdaの隠れ仕様かもしれません。

ちなみに、関数コード内でバイト列リテラルを`json.dumps`に渡したときはREPLと同じ挙動だったので、Lambdaランタイムが特殊な環境だと思います。
```python
import json

def lambda_handler(event, context):
    json.dumps(b'{"key": "value"}')
```

```
{
  "errorMessage": "Object of type bytes is not JSON serializable",
  "errorType": "TypeError",
  "requestId": "187a40d4-58cd-4bfd-92d1-e3050991cce9",
  "stackTrace": [
    "  File \"/var/task/lambda_function.py\", line 25, in lambda_handler\n    json.dumps(b'{\"key\": \"value\"}')\n",
    "  File \"/var/lang/lib/python3.9/json/__init__.py\", line 231, in dumps\n    return _default_encoder.encode(obj)\n",
    "  File \"/var/lang/lib/python3.9/json/encoder.py\", line 199, in encode\n    chunks = self.iterencode(o, _one_shot=True)\n",
    "  File \"/var/lang/lib/python3.9/json/encoder.py\", line 257, in iterencode\n    return _iterencode(o, 0)\n",
    "  File \"/var/lang/lib/python3.9/json/encoder.py\", line 179, in default\n    raise TypeError(f'Object of type {o.__class__.__name__} '\n"
  ]
}
```

## 圧縮の実装例(python)
コピペしても動きませんが、Lambda@EdgeのLambda関数コードからレスポンスを圧縮する関数を抜粋して載せておきます。
リクエストヘッダーの`Accept-Encoding`の値に応じて、brotliかgzipで圧縮するのがよいかと思います。

```python
import base64
import gzip
import brotli

BROTLI_COMPRESS_LEVEL = 4
GZIP_COMPRESS_LEVEL = 6


# 外部ネットワーク呼び出しを行って取得したレスポンスをこの関数に引き渡す
# オリジンリクエストイベントからViewerのAccept-Encodingヘッダーの値を取得して引き渡す
# responseの構造は以下のドキュメントに従う
# https://docs.aws.amazon.com/ja_jp/AmazonCloudFront/latest/DeveloperGuide/lambda-generating-http-responses-in-requests.html
def compress_response(response, accept_encoding):
    try:
        if 'br' in accept_encoding:
            response['body'] = base64.b64encode(brotli.compress(response['body'], quality=BROTLI_COMPRESS_LEVEL))
            content_encoding = 'br'
        elif 'gzip' in accept_encoding:
            response['body'] = base64.b64encode(gzip.compress(response['body'], compresslevel=GZIP_COMPRESS_LEVEL))
            content_encoding = 'gzip'
    except brotli.error:
        # brotli.compressでエラーが発生したときの処理
    except gzip.BadGzipFile:
        # gzip.compressでエラーが発生したときの処理
    else:
        # 圧縮できたらContent-Encodingヘッダーを付ける
        response['headers']['content-encoding'] = [{'key': 'Content-Encoding', 'value': content_encoding}]
    finally:
        response
```

ちなみにbrotliは標準モジュールではないので、デプロイパッケージをつくるときにpip installする必要があります。
また、brotliはモジュールにコンパイルされたバイナリを含むので、Lambdaランタイムの互換性を考慮する必要がある点に注意です。

https://x.com/datahaikuninja/status/1676745430745632768?s=20

こんな感じでinstallすれば大丈夫です。
```shell
python3.9 -m pip install \
--python-version 39 \
--platform manylinux2014_x86_64 \
--target ./package \
--implementation cp \
--only-binary=:all: --upgrade \
brotli
```

[^1]: https://docs.aws.amazon.com/ja_jp/lambda/latest/dg/python-handler.html#python-handler-return

[^2]: Runtime.MarshalErrorが発生します。https://docs.aws.amazon.com/ja_jp/lambda/latest/dg/python-exceptions.html#python-exceptions-examples

[^3]: API Gatewayのドキュメントには、Lambda関数の出力にバイトデータを含める場合は`isBase64Encode: true`に設定すると書かれています。
https://docs.aws.amazon.com/ja_jp/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html#api-gateway-simple-proxy-for-lambda-output-format
