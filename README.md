# SumireVox Backend

Discord読み上げBot「SumireVox」のバックエンドAPIサーバーです。

## 機能

- Discord OAuth2認証
- ギルド設定管理（読み上げ設定、辞書登録）
- Stripe決済連携（プレミアム機能）
- マルチBot対応

## 必要条件

- Python 3.11+
- PostgreSQL 14+
- Discord Developer Application
- Stripe Account（決済機能を使用する場合）

## セットアップ

### 1. リポジトリのクローン

```bash
git clone https://github.com/Sumire-Labs/SumireVoxBackend.git
cd SumireVoxBackend
