---
title: "AWSManagedIPDDoSListの紹介"
emoji: "🛡️"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["AWS", "Athena", "waf"]
published: true
---
# 概要
AWS WAFのマネージドルールグループの一つである`Amazon IP reputation list`に新たに追加された、`AWSManagedIPDDoSList`を紹介します。
このマネージドルールによってカウントされたリクエストをWAFのログから抽出するAthenaのクエリも載せています。

# AWSManagedIPDDoSListとは
[IP reputation rule groupsのドキュメント](https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-ip-rep.html)[^1]によると、「DDoSアクティビティに積極的に関与していることが確認されたIPアドレスを検査する」と書かれています。

[AWS Managed Rule changelogのドキュメント](https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-changelog.html)を読むと、少し詳細な説明が書かれていました。なお、この新しいマネージドルールは2022/8/30に追加されていたようです。

>この変更により、ルールグループがお客様のWebトラフィックを処理する方法が変わることはありません。
>Amazonの脅威インテリジェンスに基づき、DDoSアクティビティに積極的に関与しているIPアドレスを検査するCountアクションのルールを追加しました。

さて、このルールのアクションはカウントがデフォルトであり、いまのところはこのルール単体でリクエストをブロックすることはできないようです。
ただし、このルールにマッチしたリクエストには`awswaf:managed:aws:amazon-ip-list:AWSManagedIPDDoSList`のラベルが付くため、後続のルールでこのラベルが付与されたリクエストをブロックするカスタムルールを設定することは可能なはずです。

Amazonの脅威インテリジェンスに基づいているという記載について、AWSにはShield, ShieldAdvancedというサービスがあり、AWSとAWSに構築されたアプリケーションに向けられた世界中の攻撃に対処しているので、ShieldとShieldAdvancedの運用現場でDDoS攻撃に積極的に関与していると確認された、ということかなと推測しています。

# AWSManagedIPDDoSListの運用方法
このルール単体でリクエストをブロックしないため、このルールが付与するラベルを持つリクエストを後続のカスタムルールでブロックする運用がよいかと思います。

即時ブロックするのに抵抗がある場合は、カスタムルールのアクションをカウントにしておき、このルールでカウントされたリクエストの合計数を監視するアラームをCloudWatchやサードパーティのモニタリングツールで仕込んでおくとよさそうです。

# AWSManagedIPDDoSListでカウントされたリクエストをWebACLのログから抽出するAthenaのクエリ
テーブル定義は[こちら](https://zenn.dev/datahaikuninja/articles/select-waf-log-counted-by-managed-rule)を参考にしてください。

```sql
/*AWSManagedIPDDoSListでカウントされたリクエストのクライアントIP,国名,ヘッダー情報を取得する*/
SELECT from_unixtime(timestamp/1000, 'Asia/Tokyo') AS JST, httprequest.clientip, httprequest.country, httprequest.headers
FROM waf_logs, UNNEST(ruleGroupList) t(groupList), UNNEST(groupList.nonTerminatingMatchingRules) t(nonTermRule)
WHERE nonTermRule.ruleId = 'AWSManagedIPDDoSList'
AND day >= 'yyyy/mm/dd/hh' AND day <= 'yyyy/mm/dd/hh';

/*AWSManagedIPDDoSListでカウントされたIPアドレス,国名のカウント*/
SELECT httprequest.clientip, count(*) ipcount, httprequest.country
FROM waf_logs, UNNEST(ruleGroupList) t(groupList), UNNEST(groupList.nonTerminatingMatchingRules) t(nonTermRule)
WHERE nonTermRule.ruleId = 'AWSManagedIPDDoSList' AND day >= 'yyyy/mm/dd/hh' AND day <= 'yyyy/mm/dd/hh'
GROUP BY httprequest.clientip, httprequest.country
ORDER BY ipcount DESC
LIMIT 100;
```

`AWSManagedIPDDoSList`でカウントされたときのログの抜粋も載せておきます。
```json
[
    {
        "rulegroupid":"AWS#AWSManagedRulesAmazonIpReputationList",
        "terminatingrule":null,
        "nonterminatingmatchingrules":[
            {
                "ruleid":"AWSManagedIPDDoSList",
                "action":"COUNT",
                "rulematchdetails":[]
            }
        ],
        "excludedrules":null
    }
]
```

[^1]: 本記事の執筆時点では日本語のドキュメントには記載されていませんでした。記事の言語設定を英語にしてご覧ください。