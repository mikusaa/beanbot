# beanbot

beanbot 是一个用于写入 [Beancount](https://beancount.github.io/) 账本的私人 Telegram bot。它支持自然语言快记、按钮式录入、最近记录查询、删除 bot 写入记录，以及按规则解析常用账户和消费分类。

这个项目默认按“私人 bot”设计：只有白名单里的 Telegram 用户可以操作；真实 token、用户 ID、账本路径和个人快记规则都应该放在本地配置里，不应提交到 GitHub。

## 功能

- `/add` 或直接发送文本进行自然语言快记。
- `/expense`、`/income`、`/transfer` 使用按钮录入结构化交易。
- `/last` 查看最近一笔 bot 写入记录。
- `/delete` 从最近记录中选择并二次确认删除。
- `/reload` 重载账户列表和快记配置。
- 支持补录日期：`今天`、`昨天`、`前天`、`6月8日`、`6/8`、`2026-06-08`、`2026/06/08`。
- 支持即时优惠：如 `优惠0.5`、`优惠了5毛`、`立减1元`。
- 支持歧义账户二次确认：例如输入 `卡` 时弹出多个信用卡账户供选择。

## 账本结构

beanbot 只写入配置的 bot 输出目录，建议挂载到主账本的一个独立子目录，例如：

```text
books/
  main.bean
  config/
  data/
  tgbot/
    2026/
      06.bean
```

主账本需要 include bot 输出文件，例如：

```beancount
include "tgbot/*/*.bean"
```

bot 写入的交易会带上最少量 metadata：

```beancount
2026-06-09 * "Coffee Shop" "latte"
  source: "telegram"
  created_at: "2026-06-09 08:30:00"
  Liabilities:CreditCard:Default                         -25 CNY
  Expenses:Food:Drinks                                    25 CNY
```

如果有即时优惠，会写成三腿交易。默认把优惠记为 `Income:Discount`：

```beancount
2026-06-09 * "Restaurant" "lunch"
  source: "telegram"
  created_at: "2026-06-09 12:30:00"
  Assets:Digital:Wallet                                  -25 CNY
  Expenses:Food:Meals                                   25.5 CNY
  Income:Discount                                       -0.5 CNY
```

## 快速开始

复制示例配置：

```bash
cp .env.example .env
cp config/quickadd.example.yaml config/quickadd.yaml
```

编辑 `.env`：

```bash
TELEGRAM_BOT_TOKEN=123456789:replace-me
TELEGRAM_ALLOWED_USER_IDS=123456789
BEANCOUNT_BOOKS_DIR=/path/to/your/beancount/books
NETWORK=beancount
TZ=Asia/Shanghai
DEFAULT_CURRENCY=CNY
LOG_LEVEL=INFO
```

编辑 `config/quickadd.yaml`，把示例账户改成你的 Beancount 账户。配置里的账户必须已经在账本中 `open`，否则启动时会报错。

启动：

```bash
docker compose up -d --build beanbot
```

查看日志：

```bash
docker compose logs -f beanbot
```

## Telegram 命令

```text
/add       自然语言快记
/expense   新增支出
/income    新增收入
/transfer  新增转账
/last      查看最近一笔 bot 写入记录
/delete    删除最近 bot 写入记录
/reload    重载快记配置和账户列表
/cancel    取消当前录入
```

授权用户也可以直接发送自然语言账单，不必带 `/add`。

## 快记示例

```text
Coffee Shop latte 9.9 credit card
昨天 Coffee Shop latte 9.9 credit card
6月8日 Supermarket fruit 18 wallet
Restaurant lunch 25 card 优惠了5毛
买水果 18 钱包
昨天 咖啡店 拿铁 9.9 信用卡
```

如果配置了歧义账户：

```yaml
payment_account_choices:
  card:
    - label: Main credit card
      account: Liabilities:CreditCard:Default
    - label: Backup credit card
      account: Liabilities:CreditCard:Backup
```

输入 `Restaurant lunch 25 card` 时，bot 会先让你选择具体账户，再进入 Beancount 预览确认。

## 快记配置

`config/quickadd.yaml` 支持这些字段：

```yaml
payment_accounts:
  wallet: Assets:Digital:Wallet
  credit card: Liabilities:CreditCard:Default

payment_account_choices:
  card:
    - label: Main credit card
      account: Liabilities:CreditCard:Default
    - label: Backup credit card
      account: Liabilities:CreditCard:Backup

expense_keywords:
  Expenses:Food:Meals:
    - lunch
    - dinner
    - 吃饭

narration_stopwords:
  - lunch
  - dinner
  - 吃饭

payee_rules:
  - keywords: [Coffee Shop, 咖啡店]
    payee: Coffee Shop
    expense_account: Expenses:Food:Drinks
```

配置修改后可以发送 `/reload`，无需重启容器。

## 开发

安装依赖后运行测试：

```bash
uv run pytest
```

检查 Docker Compose 配置：

```bash
docker compose config --quiet
```

检查你的 Beancount 主账本：

```bash
bean-check /path/to/your/beancount/books/main.bean
```

## 安全与开源前检查

- 不要提交 `.env`。
- 不要提交真实 `config/quickadd.yaml`。
- 不要把 Beancount 账本文件放进这个仓库。
- token 泄露后，立刻在 BotFather 里 revoke/regenerate。
- 开源前建议扫描敏感内容：

```bash
rg -n "[0-9]{8,}:[A-Za-z0-9_-]{20,}|TELEGRAM_BOT_TOKEN|/Users|真实姓名|手机号|身份证" .
```

仓库只应该提交示例配置：

```text
.env.example
config/quickadd.example.yaml
```

本地真实配置保留但被 `.gitignore` 忽略：

```text
.env
config/quickadd.yaml
```

## License

MIT
