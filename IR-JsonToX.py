#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IR-JsonToX.py

WordPress REST APIからIR情報を取得し、X（Twitter）に自動投稿するスクリプト

使用方法:
    python IR-JsonToX.py --date 20251215 --time-start 08:00 --time-end 12:00

必要なライブラリ:
    pip install requests tweepy python-dateutil
"""

import argparse
import sys
import re
import time
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

try:
    import requests
    import tweepy
    from dateutil import parser as dateutil_parser
except ImportError as e:
    print(f"❌ 必要なライブラリがインストールされていません: {e}")
    print("以下のコマンドでインストールしてください:")
    print("pip install requests tweepy python-dateutil")
    sys.exit(1)


# ============================================================
# 定数定義
# ============================================================

# WordPress REST API設定
WORDPRESS_API_BASE = "https://japanir.jp/wp-json/wp/v2"
WORDPRESS_ENDPOINT = f"{WORDPRESS_API_BASE}/ir-release"

# カテゴリ優先順位マッピング
CATEGORY_PRIORITY = {
    # Tier 1: M&A/Alliance（最重要）
    "tender_offer": 1,
    "m_and_a_alliance": 2,
    
    # Tier 2: Earnings（決算）
    "financial_summary": 3,
    "business_update": 4,
    "earnings_guidance": 5,
    
    # Tier 3: 株主還元
    "dividend": 6,
    "share_buyback": 7,
    "capital_policy": 8,
    "share_cancellation": 9,
    
    # Tier 4: その他重要
    "corporate_restructuring": 10,
    "product_announcement": 11,
    "executive_change": 12,
    
    # Tier 5: 低優先
    "sales_update": 13,
    "esg_sustainability": 14,
    "stock_option": 15,
    "disclosure_update": 16,
    "general_ir": 17
}


# ============================================================
# ユーティリティ関数
# ============================================================

def get_importance_stars(importance_str):
    """
    重要度文字列から★の数を抽出
    
    Args:
        importance_str: 重要度文字列（例: "★4", "â˜…4"）
    
    Returns:
        int: ★の数（0-5）
    """
    if not importance_str:
        return 0
    
    # "★4" or "â˜…4" → 4
    match = re.search(r'[★☆â˜…](\d+)', str(importance_str))
    if match:
        return int(match.group(1))
    
    return 0


def get_category_priority(ir_type):
    """
    カテゴリの優先順位を取得
    
    Args:
        ir_type: IR種別（例: "tender_offer"）
    
    Returns:
        int: 優先順位（小さいほど高優先）
    """
    return CATEGORY_PRIORITY.get(ir_type, 99)


def format_datetime_for_api(date_str, time_str):
    """
    日付・時刻をWordPress API用のISO形式に変換
    
    Args:
        date_str: 日付（YYYYMMDD）
        time_str: 時刻（HH:MM）
    
    Returns:
        str: ISO形式の日時（例: "2025-12-15T08:00:00"）
    """
    date_obj = datetime.strptime(date_str, '%Y%m%d')
    time_obj = datetime.strptime(time_str, '%H:%M').time()
    dt = datetime.combine(date_obj, time_obj)
    return dt.isoformat()


# ============================================================
# WordPress REST API クラス
# ============================================================

class WordPressIRFetcher:
    """WordPress REST APIからIR情報を取得するクラス"""
    
    def __init__(self, base_url=WORDPRESS_ENDPOINT):
        self.base_url = base_url
    
    def fetch_irs(self, date_str, time_start, time_end, per_page=100):
        """
        指定日時範囲のIR情報を取得
        
        Args:
            date_str: 日付（YYYYMMDD）
            time_start: 開始時刻（HH:MM）
            time_end: 終了時刻（HH:MM）
            per_page: 取得件数（デフォルト100）
        
        Returns:
            list: IR情報のリスト
        """
        # ISO形式に変換
        after = format_datetime_for_api(date_str, time_start)
        before = format_datetime_for_api(date_str, time_end)
        
        params = {
            'per_page': per_page,
            'after': after,
            'before': before,
            'status': 'publish',
            'orderby': 'date',
            'order': 'asc',
            'lang': 'en'  # WPML: 英語投稿のみ取得
        }
        
        try:
            print(f"📡 WordPress APIからデータ取得中...")
            print(f"   エンドポイント: {self.base_url}")
            print(f"   時刻範囲: {time_start} - {time_end}")
            
            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            print(f"✅ {len(data)}件のIR情報を取得しました")
            
            return data
        
        except requests.exceptions.Timeout:
            print(f"⚠️ WordPress APIタイムアウト（30秒）")
            return []
        
        except requests.exceptions.RequestException as e:
            print(f"❌ WordPress API エラー: {e}")
            return []


# ============================================================
# IRデータ処理クラス
# ============================================================

class IRDataProcessor:
    """IR情報のフィルタ・ソート・選定を行うクラス"""
    
    def __init__(self):
        pass
    
    def extract_ir_info(self, wp_post):
        """
        WordPress投稿からIR情報を抽出
        
        Args:
            wp_post: WordPress REST APIのレスポンス（1件分）
        
        Returns:
            dict: 整形されたIR情報
        """
        meta = wp_post.get('meta', {})
        
        return {
            'id': wp_post.get('id'),
            'date': wp_post.get('date'),
            'stock_code': meta.get('jir_stock_code', ''),
            'company_name': meta.get('jir_company_name', ''),
            'ir_type': meta.get('jir_ir_type', ''),
            'importance': meta.get('jir_importance', ''),
            'short_summary': meta.get('jir_short_summary', ''),
            'link': wp_post.get('link', '')
        }
    
    def filter_by_importance(self, ir_list):
        """
        重要度でフィルタ（★4以上、なければ★3以下も含める）
        
        Args:
            ir_list: IR情報のリスト
        
        Returns:
            list: フィルタ後のIR情報
        """
        # ★4以上
        filtered_4plus = [ir for ir in ir_list 
                          if get_importance_stars(ir['importance']) >= 4]
        
        if len(filtered_4plus) > 0:
            print(f"📊 重要度フィルタ: ★4以上 {len(filtered_4plus)}件")
            return filtered_4plus
        
        # ★3以下も含める
        filtered_3plus = [ir for ir in ir_list 
                          if get_importance_stars(ir['importance']) >= 3]
        
        print(f"📊 重要度フィルタ: ★3以上 {len(filtered_3plus)}件（★4以上が0件のため）")
        return filtered_3plus
    
    def sort_by_priority(self, ir_list):
        """
        カテゴリ優先順位 + 重要度でソート
        
        Args:
            ir_list: IR情報のリスト
        
        Returns:
            list: ソート後のIR情報
        """
        return sorted(ir_list, key=lambda x: (
            get_category_priority(x['ir_type']),      # カテゴリ優先順位
            -get_importance_stars(x['importance'])    # 重要度（降順）
        ))
    
    def remove_duplicate_companies(self, ir_list):
        """
        同一企業の重複を排除（最優先IRのみ残す）
        
        Args:
            ir_list: IR情報のリスト（ソート済み）
        
        Returns:
            list: 重複排除後のIR情報
        """
        seen_companies = set()
        result = []
        
        for ir in ir_list:
            stock_code = ir['stock_code']
            
            if not stock_code:
                continue
            
            if stock_code not in seen_companies:
                seen_companies.add(stock_code)
                result.append(ir)
        
        if len(ir_list) != len(result):
            print(f"🔄 重複排除: {len(ir_list)}件 → {len(result)}件")
        
        return result
    
    def select_top_n(self, ir_list, n=5):
        """
        Top N選定（最大5件）
        
        Args:
            ir_list: IR情報のリスト
            n: 選定件数（デフォルト5）
        
        Returns:
            list: Top N のIR情報
        """
        if len(ir_list) >= n:
            print(f"🎯 Top {n}選定: {len(ir_list)}件から{n}件を選定")
            return ir_list[:n]
        
        print(f"🎯 Top {n}選定: {len(ir_list)}件（全件採用）")
        return ir_list


# ============================================================
# X投稿文生成クラス
# ============================================================

class TweetGenerator:
    """X投稿文を生成するクラス"""
    
    def __init__(self, max_length=2000):
        self.max_length = max_length
    
    def generate_tweet(self, ir_list, date_str):
        """
        X投稿文を生成（Pattern 3: Card Style）

        Args:
            ir_list: Top N のIR情報リスト
            date_str: 日付（YYYYMMDD）

        Returns:
            str: 投稿文
        """
        # 日付フォーマット: December 23, 2025
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        formatted_date = date_obj.strftime('%B %d, %Y')

        # ヘッダー（Pattern 3スタイル）
        lines = [
            f"🇯🇵 JapanIR Highlights",
            f"{formatted_date}",
            ""
        ]

        # 各IR（Card Style）
        for ir in ir_list:
            stock_code = ir['stock_code']
            company_name = ir['company_name']
            ir_type = self._format_ir_type(ir['ir_type'])
            summary = ir['short_summary']

            # Card Style: 企業名 + コード
            lines.append(f"▪️ {company_name} ({stock_code})")
            # サマリー + カテゴリタグ
            lines.append(f"   {summary} [{ir_type}]")
            lines.append("")

        # フッター
        lines.append("📊 japanir.jp/en")
        lines.append("#JapanStocks #IR")

        tweet = "\n".join(lines)

        # 文字数チェック
        if len(tweet) > self.max_length:
            print(f"⚠️ 投稿文が長すぎます: {len(tweet)}文字（最大{self.max_length}文字）")
            tweet = tweet[:self.max_length - 80] + "\n\n...\n\n📊 japanir.jp/en\n#JapanStocks #IR"

        print(f"📝 投稿文生成完了: {len(tweet)}文字")
        return tweet

    def _format_ir_type(self, ir_type):
        """
        IR種別を表示用にフォーマット

        Args:
            ir_type: IR種別（例: "financial_summary"）

        Returns:
            str: 表示用の種別名
        """
        type_mapping = {
            'financial_summary': 'Earnings',
            'share_buyback': 'Share Buyback',
            'share_cancellation': 'Share Cancellation',
            'dividend': 'Dividend',
            'earnings_guidance': 'Guidance',
            'executive_change': 'Executive',
            'sales_update': 'Sales Update',
            'disclosure_update': 'Disclosure',
            'capital_policy': 'Capital Policy',
            'tender_offer': 'TOB',
            'corporate_restructuring': 'Restructuring',
            'm_and_a_alliance': 'M&A',
            'product_announcement': 'Product',
            'business_update': 'Business Update',
            'stock_option': 'Stock Option',
            'esg_sustainability': 'ESG',
            'general_ir': 'IR'
        }
        return type_mapping.get(ir_type, ir_type.replace('_', ' ').title())


# ============================================================
# X投稿クラス
# ============================================================

class TwitterPoster:
    """Xに投稿するクラス"""
    
    def __init__(self, api_key, api_secret, access_token, access_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.access_secret = access_secret
    
    def post(self, tweet_text):
        """
        Xに投稿
        
        Args:
            tweet_text: 投稿文
        
        Returns:
            bool: 成功/失敗
        """
        try:
            print(f"🐦 X投稿実行中...")
            
            # tweepy v2 Client初期化
            client = tweepy.Client(
                consumer_key=self.api_key,
                consumer_secret=self.api_secret,
                access_token=self.access_token,
                access_token_secret=self.access_secret
            )
            
            # 投稿実行
            response = client.create_tweet(text=tweet_text)
            
            tweet_id = response.data['id']
            print(f"✅ X投稿成功: ID {tweet_id}")
            print(f"   URL: https://twitter.com/user/status/{tweet_id}")
            
            return True
        
        except tweepy.errors.TweepyException as e:
            print(f"❌ X投稿エラー: {e}")
            return False


# ============================================================
# Gmail通知クラス
# ============================================================

class GmailNotifier:
    """エラー通知をGmailで送信するクラス"""
    
    def __init__(self, sender_email, password, receiver_email):
        self.sender_email = sender_email
        self.password = password
        self.receiver_email = receiver_email
    
    def send_error_notification(self, error_message, date_str, time_range):
        """
        エラー通知メールを送信
        
        Args:
            error_message: エラーメッセージ
            date_str: 日付
            time_range: 時間帯
        """
        subject = f"[JapanIR] X投稿失敗 - {date_str} {time_range}"
        body = f"""
JapanIR X投稿処理でエラーが発生しました。

日付: {date_str}
時間帯: {time_range}

エラー内容:
{error_message}

対応をお願いします。
"""
        
        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        msg['To'] = self.receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(self.sender_email, self.password)
            server.send_message(msg)
            server.quit()
            print("✅ エラー通知メール送信成功")
        except Exception as e:
            print(f"❌ エラー通知メール送信失敗: {e}")


# ============================================================
# メイン処理
# ============================================================

def main(date_str, time_start, time_end, dry_run=False):
    """
    メイン処理
    
    Args:
        date_str: 日付（YYYYMMDD）
        time_start: 開始時刻（HH:MM）
        time_end: 終了時刻（HH:MM）
        dry_run: テストモード（True=投稿しない）
    
    Returns:
        bool: 成功/失敗
    """
    print("=" * 60)
    print("🚀 IR-JsonToX.py 開始")
    if dry_run:
        print("🧪 【テストモード】投稿文生成のみ（X投稿しない）")
    print("=" * 60)
    print(f"日付: {date_str}")
    print(f"時刻範囲: {time_start} - {time_end}")
    print("")
    
    # ステップ1: WordPress REST APIからデータ取得
    fetcher = WordPressIRFetcher()
    wp_posts = fetcher.fetch_irs(date_str, time_start, time_end)
    
    if len(wp_posts) == 0:
        print("ℹ️ 該当するIR情報がありませんでした")
        return True  # エラーではない
    
    # ステップ2: IR情報抽出
    processor = IRDataProcessor()
    ir_list = [processor.extract_ir_info(post) for post in wp_posts]
    
    # ステップ3: 重要度フィルタ（スキップ - 全件対象）
    # ir_list = processor.filter_by_importance(ir_list)
    # 
    # if len(ir_list) == 0:
    #     print("ℹ️ ★3以上のIR情報がありませんでした")
    #     return True  # エラーではない
    
    print(f"📊 全件対象: {len(ir_list)}件（重要度フィルタなし）")
    
    # ステップ4: カテゴリ優先順位ソート
    ir_list = processor.sort_by_priority(ir_list)
    
    # ステップ5: 同一企業重複排除
    ir_list = processor.remove_duplicate_companies(ir_list)
    
    # ステップ6: Top 5選定
    ir_list = processor.select_top_n(ir_list, 5)
    
    # ステップ7: 投稿文生成
    generator = TweetGenerator()
    tweet_text = generator.generate_tweet(ir_list, date_str)
    
    print("")
    print("=" * 60)
    print("📄 生成された投稿文:")
    print("=" * 60)
    print(tweet_text)
    print("=" * 60)
    print("")
    
    # ステップ8: X投稿実行
    if dry_run:
        print("🧪 テストモード: X投稿をスキップします")
        print("")
        print("=" * 60)
        print("✅ テスト完了（投稿文のみ生成）")
        print("=" * 60)
        return True
    
    api_key = os.getenv('X_API_KEY')
    api_secret = os.getenv('X_API_SECRET')
    access_token = os.getenv('X_ACCESS_TOKEN')
    access_secret = os.getenv('X_ACCESS_SECRET')
    
    if not all([api_key, api_secret, access_token, access_secret]):
        print("⚠️ X API認証情報が設定されていません（環境変数）")
        print("   投稿文の生成のみ実行しました")
        return True
    
    poster = TwitterPoster(api_key, api_secret, access_token, access_secret)
    success = poster.post(tweet_text)
    
    return success


def main_with_retry(date_str, time_start, time_end, dry_run=False, max_retries=3):
    """
    メイン処理（リトライ付き）
    
    Args:
        date_str: 日付（YYYYMMDD）
        time_start: 開始時刻（HH:MM）
        time_end: 終了時刻（HH:MM）
        dry_run: テストモード
        max_retries: 最大リトライ回数
    
    Returns:
        bool: 成功/失敗
    """
    for attempt in range(1, max_retries + 1):
        try:
            print(f"\n📝 試行 {attempt}/{max_retries}")
            
            success = main(date_str, time_start, time_end, dry_run)
            
            if success:
                print("\n" + "=" * 60)
                print("✅ 処理が正常に完了しました")
                print("=" * 60)
                return True
            
        except Exception as e:
            error_msg = f"試行 {attempt} 失敗: {str(e)}"
            print(f"\n❌ {error_msg}")
            
            if attempt == max_retries:
                # 最終リトライ失敗 → メール通知
                gmail_password = os.getenv('GMAIL_APP_PASSWORD')
                if gmail_password:
                    notifier = GmailNotifier(
                        "japanir100@gmail.com",
                        gmail_password,
                        "japanir100@gmail.com"
                    )
                    notifier.send_error_notification(
                        error_message=error_msg,
                        date_str=date_str,
                        time_range=f"{time_start}-{time_end}"
                    )
                return False
            
            # 次のリトライ前に待機
            print(f"⏳ 10秒待機してリトライします...")
            time.sleep(10)
    
    return False


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='WordPress REST APIからIR情報を取得してX投稿'
    )
    parser.add_argument(
        '--date',
        required=True,
        help='日付（YYYYMMDD形式）例: 20251215'
    )
    parser.add_argument(
        '--time-start',
        required=True,
        help='開始時刻（HH:MM形式）例: 08:00'
    )
    parser.add_argument(
        '--time-end',
        required=True,
        help='終了時刻（HH:MM形式）例: 12:00'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='テストモード（投稿文生成のみ、X投稿しない）'
    )
    
    args = parser.parse_args()
    
    # 引数バリデーション
    try:
        datetime.strptime(args.date, '%Y%m%d')
        datetime.strptime(args.time_start, '%H:%M')
        datetime.strptime(args.time_end, '%H:%M')
    except ValueError as e:
        print(f"❌ 引数のフォーマットエラー: {e}")
        sys.exit(1)
    
    # メイン処理実行
    success = main_with_retry(
        args.date, 
        args.time_start, 
        args.time_end,
        dry_run=args.dry_run
    )
    
    sys.exit(0 if success else 1)