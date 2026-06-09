# beanbot

beanbot 是一个面向个人使用的 Telegram bot，用来把日常账单写入 [Beancount](https://beancount.github.io/) 账本。它可以解析自然语言快记，也提供按钮式的支出、收入和转账录入流程。

## 功能

- 自然语言快记：直接发送一句账单，或使用 `/add`。
- 按钮式录入：通过 `/expense`、`/income`、`/transfer` 分步选择账户。
- 最近记录：通过 `/last` 查看最近一笔 bot 写入的交易。
- 删除记录：通过 `/delete` 选择最近交易，二次确认后删除。
- 配置热重载：修改快记规则后发送 `/reload` 生效。
- 日期补录：支持 `昨天`、`前天`、`6月8日`、`2026-06-08` 等日期表达。
- 优惠记录：支持 `优惠0.5`、`优惠了5毛`、`立减1元` 等即时优惠。
- 歧义账户选择：同一个别名可以配置多个候选账户，由 Telegram 按钮确认。

## 账本写入

beanbot 只写入配置的 bot 输出目录，推荐在主账本旁边单独放一个目录：

```text
books/
  main.bean
  config/
  data/
  tgbot/
    2026/
      06.bean
```

主账本中 include bot 生成的文件：

```beancount
include "tgbot/*/*.bean"
```

普通支出示例：

```beancount
2026-06-09 * "Coffee Shop" "latte"
  source: "telegram"
  created_at: "2026-06-09 08:30:00"
  Liabilities:CreditCard:Default                         -25 CNY
  Expenses:Food:Drinks                                    25 CNY
```

带即时优惠的支出会写成三腿交易，默认把优惠记到 `Income:Discount`：

```beancount
2026-06-09 * "Restaurant" "lunch"
  source: "telegram"
  created_at: "2026-06-09 12:30:00"
  Assets:Digital:Wallet                                  -25 CNY
  Expenses:Food:Meals                                   25.5 CNY
  Income:Discount                                       -0.5 CNY
```

## 快速开始

复制配置模板：

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

编辑 `config/quickadd.yaml`，把示例账户改成你的 Beancount 账户。配置中引用的账户必须已经在账本中 `open`。

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

如果 `card` 配置了多个候选账户：

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

快记规则写在 `config/quickadd.yaml`。完整示例见 `config/quickadd.example.yaml`。

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

字段说明：

- `payment_accounts`：确定性的付款账户别名。
- `payment_account_choices`：需要二次确认的账户别名。
- `expense_keywords`：根据关键词推断支出分类。
- `narration_stopwords`：生成 narration 时移除的分类词。
- `payee_rules`：根据关键词固定交易对象和支出分类。

修改配置后发送 `/reload` 即可生效。

## 开发

运行测试：

```bash
uv run pytest
```

检查 Docker Compose 配置：

```bash
docker compose config --quiet
```

检查 Beancount 主账本：

```bash
bean-check /path/to/your/beancount/books/main.bean
```

## 安全

beanbot 按私人 bot 设计，只允许 `TELEGRAM_ALLOWED_USER_IDS` 中的用户操作。不要提交 `.env`、真实 `config/quickadd.yaml` 或任何账本数据。若 Telegram bot token 泄露，请在 BotFather 中重新生成。

## License

MIT
