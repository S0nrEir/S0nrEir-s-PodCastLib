#!/usr/bin/env bash
# 播客选题库 API 集成测试（Git Bash + Windows 环境适配：中文走文件、临时文件放工作区）
set -u
BASE=${BASE:-http://127.0.0.1:8390}
WS=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$WS/data"
TMP=$(mktemp -d "$WS/data/api-test.XXXXXX") || exit 1
if command -v cygpath >/dev/null 2>&1; then
  WPY=$(cygpath -m "$TMP")  # Windows Python 可用的等价路径
else
  WPY=$TMP
fi
JAR=$TMP/cookies.txt
PASS=0; FAIL=0

check() { # check <名称> <期望> <实际>
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "ok   - $1";
  else FAIL=$((FAIL+1)); echo "FAIL - $1 (期望=[$2] 实际=[$3])"; fi
}

jqget() { python -c "import json,sys;d=json.load(sys.stdin);print(eval('d'+sys.argv[1]))" "$1" 2>/dev/null; }

# 生成 UTF-8 JSON 文件并输出路径
mkbody() { printf '%s' "$1" > "$TMP/req.json"; echo "$TMP/req.json"; }

curlj() { # curlj METHOD URL [JSON字符串]
  local m=$1 u=$2 b=${3:-}
  if [ -n "$b" ]; then
    local f; f=$(mkbody "$b")
    curl -s -b "$JAR" -c "$JAR" -X "$m" "$u" -H 'Content-Type: application/json' --data-binary @"$f"
  else
    curl -s -b "$JAR" -c "$JAR" -X "$m" "$u"
  fi
}
statusj() { # 同 curlj 但只返回 http code
  local m=$1 u=$2 b=${3:-}
  if [ -n "$b" ]; then
    local f; f=$(mkbody "$b")
    curl -s -b "$JAR" -c "$JAR" -o /dev/null -w '%{http_code}' -X "$m" "$u" -H 'Content-Type: application/json' --data-binary @"$f"
  else
    curl -s -b "$JAR" -c "$JAR" -o /dev/null -w '%{http_code}' -X "$m" "$u"
  fi
}

echo "== 1. 未初始化时 state =="
check "state 未初始化" "False" "$(curl -s $BASE/api/state | jqget "['initialized']")"

echo "== 2. 初始化设置密码 =="
R=$(curlj POST $BASE/api/setup '{"password":"test123456"}')
check "setup 成功" "True" "$(echo "$R" | jqget "['ok']")"
check "重复 setup 被拒(409)" "409" "$(statusj POST $BASE/api/setup '{"password":"another123"}')"
check "弱密码被拒(400)" "400" "$(statusj POST $BASE/api/setup '{"password":"123"}')"

echo "== 4. 会话已建立，可访问数据 =="
check "topics 需要登录(无cookie返回401)" "401" "$(curl -s -o /dev/null -w '%{http_code}' $BASE/api/topics)"
check "带会话访问 topics" "200" "$(statusj GET $BASE/api/topics)"

echo "== 5. 建选题 =="
R=$(curlj POST $BASE/api/topics '{"title":"为什么我们停不下来刷手机","notes":"注意力经济"}')
TID=$(echo "$R" | jqget "['topic']['id']")
check "创建选题返回 id" "1" "$TID"
check "中文标题正确" "为什么我们停不下来刷手机" "$(curl -s -b "$JAR" $BASE/api/topics | jqget "['topics'][0]['title']")"
check "空标题被拒(400)" "400" "$(statusj POST $BASE/api/topics '{"title":"  "}')"

echo "== 6. 加三条分类内容 =="
R=$(curlj POST $BASE/api/topics/$TID/items '{"category":"like","content":"多巴胺机制的讲解很清晰"}')
I1=$(echo "$R" | jqget "['item']['id']")
R=$(curlj POST $BASE/api/topics/$TID/items '{"category":"dislike","content":"例子太美国化"}')
I2=$(echo "$R" | jqget "['item']['id']")
R=$(curlj POST $BASE/api/topics/$TID/items '{"category":"thought","content":"联想到老虎机的设计"}')
I3=$(echo "$R" | jqget "['item']['id']")
check "三个条目 id 均有效" "3" "$([ -n "$I1" ] && [ -n "$I2" ] && [ -n "$I3" ] && echo 3 || echo 0)"
check "非法 category 被拒" "400" "$(statusj POST $BASE/api/topics/$TID/items '{"category":"xxx","content":"y"}')"
check "空内容被拒" "400" "$(statusj POST $BASE/api/topics/$TID/items '{"category":"like","content":"  "}')"

echo "== 7. 列表内容正确 =="
R=$(curl -s -b "$JAR" $BASE/api/topics)
check "选题数=1" "1" "$(echo "$R" | jqget "['topics'].__len__()")"
check "每条选题 items=3" "3" "$(echo "$R" | jqget "['topics'][0]['items'].__len__()")"

echo "== 8. 改选题/改内容/删内容 =="
check "改标题" "200" "$(statusj PUT $BASE/api/topics/$TID '{"title":"为什么我们停不下来刷手机（改）"}')"
check "改内容" "200" "$(statusj PUT $BASE/api/items/$I1 '{"content":"多巴胺机制讲得非常清楚"}')"
check "删内容" "200" "$(statusj DELETE $BASE/api/items/$I2)"
check "删除后 items=2" "2" "$(curl -s -b "$JAR" $BASE/api/topics | jqget "['topics'][0]['items'].__len__()")"
check "改不存在的选题(404)" "404" "$(statusj PUT $BASE/api/topics/99999 '{"title":"x"}')"
check "删不存在的选题(404)" "404" "$(statusj DELETE $BASE/api/topics/99999)"

echo "== 9. 排序设置 =="
check "改排序 title/asc" "200" "$(statusj PUT $BASE/api/settings '{"sort_field":"title","sort_order":"asc"}')"
curlj POST $BASE/api/topics '{"title":"阿巴克排序测试"}' > /dev/null
R=$(curl -s -b "$JAR" $BASE/api/topics)
# SQLite 按码点排序: 为(U+4E3A) < 阿(U+963F)
check "title 升序第一条" "为什么我们停不下来刷手机（改）" "$(echo "$R" | jqget "['topics'][0]['title']")"
statusj PUT $BASE/api/settings '{"sort_field":"title","sort_order":"desc"}' > /dev/null
R=$(curl -s -b "$JAR" $BASE/api/topics)
check "title 降序第一条" "阿巴克排序测试" "$(echo "$R" | jqget "['topics'][0]['title']")"
ATID=$(echo "$R" | jqget "['topics'][0]['id']")
statusj DELETE $BASE/api/topics/$ATID > /dev/null
check "非法排序字段被拒" "400" "$(statusj PUT $BASE/api/settings '{"sort_field":"evil","sort_order":"asc"}')"
statusj PUT $BASE/api/settings '{"sort_field":"updated_at","sort_order":"desc"}' > /dev/null

echo "== 10. 导出 =="
R=$(curl -s -b "$JAR" $BASE/api/export)
printf '%s' "$R" > "$TMP/export.json"
check "导出含 1 个选题" "1" "$(echo "$R" | jqget "['topics'].__len__()")"
check "导出 app 字段" "podcast-topic-library" "$(echo "$R" | jqget "['app']")"
check "导出带 content-disposition" "1" "$(curl -s -b "$JAR" -D - -o /dev/null $BASE/api/export | grep -ci 'content-disposition')"

echo "== 11. 导入(追加) =="
curlj POST $BASE/api/topics '{"title":"原始记录"}' > /dev/null
python - "$WPY" <<'PY'
import json
import pathlib
import sys
tmp = pathlib.Path(sys.argv[1])
data = json.loads((tmp / 'export.json').read_text(encoding='utf-8'))
out = {'data': data, 'mode': 'append'}
(tmp / 'import.json').write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
PY
R=$(curl -s -b "$JAR" -X POST $BASE/api/import -H 'Content-Type: application/json' --data-binary @"$TMP/import.json")
check "追加导入 1 选题" "1" "$(echo "$R" | jqget "['topics']")"
check "导入后总数=3" "3" "$(curl -s -b "$JAR" $BASE/api/topics | jqget "['topics'].__len__()")"
check "空导入被拒(400)" "400" "$(statusj POST $BASE/api/import '{"data":{"topics":[]},"mode":"append"}')"
check "烂格式被拒(400)" "400" "$(statusj POST $BASE/api/import '{"data":{"foo":1},"mode":"append"}')"

echo "== 12. 修改密码 + 旧密码失效 =="
check "旧密码错误时改密失败" "401" "$(statusj POST $BASE/api/password '{"old_password":"wrongwrong","new_password":"newpass999"}')"
check "改密码" "200" "$(statusj POST $BASE/api/password '{"old_password":"test123456","new_password":"newpass999"}')"
check "旧密码登录失败" "401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/api/login -H 'Content-Type: application/json' -d '{"password":"test123456"}')"
rm -f "$JAR"
check "新密码登录成功" "200" "$(curl -s -c "$JAR" -o /dev/null -w '%{http_code}' -X POST $BASE/api/login -H 'Content-Type: application/json' -d '{"password":"newpass999"}')"
check "错误密码登录失败" "401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/api/login -H 'Content-Type: application/json' -d '{"password":"wrongwrong"}')"

echo "== 13. 登出后会话失效 =="
statusj POST $BASE/api/logout > /dev/null
check "登出后 topics 401" "401" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' $BASE/api/topics)"

echo "== 14. 静态页 =="
check "首页 200" "200" "$(curl -s -o /dev/null -w '%{http_code}' $BASE/)"
check "css 200" "200" "$(curl -s -o /dev/null -w '%{http_code}' $BASE/static/style.css)"
check "js 200" "200" "$(curl -s -o /dev/null -w '%{http_code}' $BASE/static/app.js)"
check "路径穿越被拒" "404" "$(curl -s -o /dev/null -w '%{http_code}' --path-as-is $BASE/static/../server.py)"
check "未知 api 404" "404" "$(curl -s -o /dev/null -w '%{http_code}' $BASE/api/nope)"

echo ""
echo "=============================="
echo "通过 $PASS 项，失败 $FAIL 项"
[ "$FAIL" -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit $FAIL
