---
title: "AWS WAFのマネージドルールでカウントされたリクエストをWAFのログから抽出するAthenaのクエリ"
emoji: "🔍"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["AWS", "Athena", "waf"]
published: false
---
# 概要
AWS WAFのマネージドルールでカウントされたリクエストをWAFのログから抽出するAthenaのクエリを紹介します。

# 背景
AWS WAFのマネージドルールを使うとき、ルールアクションをまずはカウントにして様子を見てからブロックに変更すると思います。

WAFのCloudWatchメトリクスを見て、あるリクエストがいつ・どのルールによってカウントされたかを知ることができますが、メトリクスをみるだけでは対象のルールアクションをカウントからブロックへ変更してよいものか判断できません。

そこでWAFのWebACLログから、カウントされたリクエストのIPやヘッダー等の情報を確認してルールアクションを変更するか判断する必要があります。

WAFのログをAthenaで分析するためのクエリは公式ドキュメントやブログ等で紹介されていますが、自分の用途にぴったりのクエリが見つからなかったのでこの記事を書いています。

また、公式ドキュメントに記載されているテーブル[^1]をそのまま使うと、`ruleMatchDetails`というSQL インジェクションおよびクロスサイトスクリプティング (XSS) 一致ルールステートメントに対してのみ設定されるフィールド情報[^2]が取得できなかったので、テーブルを変更しました。こちらも紹介します。

# テーブル
::: details 変更後のWAFログ用テーブル
```sql
CREATE EXTERNAL TABLE `waf_logs`(
  `timestamp` bigint,
  `formatversion` int,
  `webaclid` string,
  `terminatingruleid` string,
  `terminatingruletype` string,
  `action` string,
  `terminatingrulematchdetails` array<
                                  struct<
                                    conditiontype:string,
                                    location:string,
                                    matcheddata:array<string>
                                        >
                                     >,
  `httpsourcename` string,
  `httpsourceid` string,
  `rulegrouplist` array<
                     struct<
                        rulegroupid:string,
                        terminatingrule:struct<
                           ruleid:string,
                           action:string,
                           rulematchdetails:string
                                               >,
                        nonterminatingmatchingrules:array</*（今回の記事とは関係ないが）公式ドキュメントだとarray<string>になっている。*/
                                                       struct<
                                                          ruleid:string,
                                                          action:string,
                                                          rulematchdetails:array<
                                                               struct<
                                                                  conditiontype:string,
                                                                  location:string,
                                                                  matcheddata:array<string>
                                                                     >
                                                                  >
                                                               >
                                                            >,
                        excludedrules:array< /*公式ドキュメントだとstringになっている。今回の記事に関係のある変更*/
                                         struct<
                                            ruleid:string,
                                            exclusiontype:string,
                                            rulematchdetails:array<
                                             struct<
                                                conditiontype:string,
                                                location:string,
                                                matcheddata:array<string>
                                                   >
                                                >
                                               >
                                            >
                           >
                       >,
  `ratebasedrulelist` array<
                        struct<
                          ratebasedruleid:string,
                          limitkey:string,
                          maxrateallowed:int
                              >
                           >,
  `nonterminatingmatchingrules` array<
                                  struct<
                                    ruleid:string,
                                    action:string
                                        >
                                     >,
  `requestheadersinserted` string,
  `responsecodesent` string,
  `httprequest` struct<
                      clientip:string,
                      country:string,
                      headers:array<
                                struct<
                                  name:string,
                                  value:string
                                      >
                                   >,
                      uri:string,
                      args:string,
                      httpversion:string,
                      httpmethod:string,
                      requestid:string
                      >,
  `labels` array<
             struct<
               name:string
                   >
                  >
)
PARTITIONED BY
(
 day STRING
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://bucketname/AWSLogs/accountID/WAFLogs/cloudfront/webACLName/'
TBLPROPERTIES
(
 "projection.enabled" = "true",
 "projection.day.type" = "date",
 "projection.day.range" = "2022/01/01/00,NOW",
 "projection.day.format" = "yyyy/MM/dd/HH",
 "projection.day.interval" = "1",
 "projection.day.interval.unit" = "HOURS",
 "storage.location.template" = "s3://bucketname/AWSLogs/accountID/WAFLogs/cloudfront/webACLName/${day}"
)
```
:::

実際のWebACLのログを眺めつつテーブルを更新しています。
`rulegrouplist`の配列は以下のようになっていました。

::: details WebACLログの抜粋
```json
"ruleGroupList":[
      {
        "ruleGroupId":"AWS#AWSManagedRulesAmazonIpReputationList",
        "terminatingRule":null,
        "nonTerminatingMatchingRules":[],
        "excludedRules":null,
        "ruleActionOverrides":null
      },
      {
        "ruleGroupId":"AWS#AWSManagedRulesAnonymousIpList",
        "terminatingRule":null,
        "nonTerminatingMatchingRules":[],
        "excludedRules":[
          {
            "exclusionType":"EXCLUDED_AS_COUNT",
            "ruleId":"HostingProviderIPList",
            "ruleMatchDetails":null
          }
        ],
        "ruleActionOverrides":null
      },
      {
        "ruleGroupId":"AWS#AWSManagedRulesCommonRuleSet",
        "terminatingRule":null,
        "nonTerminatingMatchingRules":[],
        "excludedRules":[
          {
            "exclusionType":"EXCLUDED_AS_COUNT",
            "ruleId":"CrossSiteScripting_COOKIE",
            "ruleMatchDetails":[
              {
                "conditionType":"XSS",
                "location":"HEADER",
                "matchedData":[
                  "マッチした文字列","マッチした文字列"
                ]
              }
            ]
          }
        ],
        "ruleActionOverrides":null
      },
      {
        "ruleGroupId":"AWS#AWSManagedRulesKnownBadInputsRuleSet",
        "terminatingRule":null,
        "nonTerminatingMatchingRules":[],
        "excludedRules":null,
        "ruleActionOverrides":null
      },
      {
        "ruleGroupId":"AWS#AWSManagedRulesPHPRuleSet",
        "terminatingRule":null,
        "nonTerminatingMatchingRules":[],
        "excludedRules":null,
        "ruleActionOverrides":null
      },
      {
        "ruleGroupId":"AWS#AWSManagedRulesSQLiRuleSet",
        "terminatingRule":null,
        "nonTerminatingMatchingRules":[],
        "excludedRules":null,
        "ruleActionOverrides":null
      }
  ]
```
:::
クロスサイトスクリプティング(XSS)としてカウントされたリクエストヘッダー値のどの部分がルールにマッチしたか知るには`matchedData`を見る必要があるのですが、公式ドキュメントで紹介されているテーブルだと取得できなかったので試行錯誤して更新しました。[^3]

# クエリ例
例えば、次のクエリでコアルールセットの`CrossSiteScripting_COOKIE`でカウントされたリクエストの情報を抽出できます。
```sql
/*
マネージドルールグループ内のルールでカウントされたリクエストを抽出する
exclude.ruleId = '' に指定するルール名は以下のドキュメントから探す
https://docs.aws.amazon.com/ja_jp/waf/latest/developerguide/aws-managed-rule-groups-list.html
*/
SELECT from_unixtime(timestamp/1000, 'Asia/Tokyo') AS JST, groupList.ruleGroupId, excludeRule, httprequest.clientip, httprequest.country, httprequest.headers, httprequest.uri, httprequest.args, httprequest.httpMethod
FROM waf_logs, UNNEST(ruleGroupList) t(groupList), UNNEST(groupList.excludedRules) t(excludeRule)
WHERE excludeRule.ruleId = 'CrossSiteScripting_COOKIE' /*コアルールセットの'CrossSiteScripting_COOKIE'でカウントされたリクエスト*/
AND day >= 'yyyy/mm/dd/hh' AND day <= 'yyyy/mm/dd/hh'; /*UTC表記で指定する。WAFのログが格納されているS3の階層と一致する。*/
```
`excludeRule.ruleId = ''`に指定するルール名を変更することで、コアルールセット以外のマネージドルールグループに含まれるルールでカウントされたリクエストの抽出も可能です。

例えば、次のクエリで`AnonymousIPList`のルールでカウントされたIPと国コード情報を計上できます。
```sql
SELECT httprequest.clientip, count(*) ipcount, httprequest.country
FROM waf_logs, UNNEST(ruleGroupList) t(groupList), UNNEST(groupList.excludedRules) t(excludeRule)
WHERE excludeRule.ruleId = 'AnonymousIPList' AND day >= 'yyyy/mm/dd/hh' AND day <= 'yyyy/mm/dd/hh'
GROUP BY httprequest.clientip, httprequest.country
ORDER BY ipcount DESC
LIMIT 100;
```

[^1]: https://docs.aws.amazon.com/ja_jp/athena/latest/ug/waf-logs.html#create-waf-table-partition-projection
[^2]: https://docs.aws.amazon.com/ja_jp/waf/latest/developerguide/logging-fields.html
[^3]: 余計な変更があるかもしれません。