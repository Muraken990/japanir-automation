#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IR-JsonToX.py

WordPress REST APIからIR情報を取得し、X（Twitter）に自動投稿するスクリプト
画像添付機能追加版

使用方法:
    python IR-JsonToX.py --date 20251215 --time-start 08:00 --time-end 12:00 --image-path image.png

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
    """重要度文字列から★の数を抽出"""
    if not importance_str:
        return 0
    match = re.search(r'[★☆â˜…](\d+)', str(importance_str))
    if match:
        return int(match.group(1))
    return 0


def get_category_priority(ir_type):
    """カテゴリの優先順位を取得"""
    return CATEGORY_PRIORITY.get(ir_type, 99)


def format_datetime_for_api(date_str, time_str):
    """日付・時刻をWordPress API用のISO形式に変換"""
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
        """指定日時範囲のIR情報を取得"""
        after = format_datetime_for_api(date_str, time_start)
        before = format_datetime_for_api(date_str, time_end)
        
        params = {
            'per_page': per_page,
            'after': after,
            'before': before,
            'status': 'publish',
            'orderby': 'date',
            'order': 'asc',
            'lang': 'en'
        }
        
        try:
            print(f"📡 WordPress APIからデータ取得中...")
            print(f"   時刻範囲: {time_start} - {time_end}")
            
            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            print(f"✅ {len(data)}件のIR情報を取得しました")
            
            return data
        
        except requests.exceptions.RequestException as e:
            print(f"❌ WordPress API エラー: {e}")
            return []


# ============================================================
# IRデータ処理クラス
# ============================================================

class IRDataProcessor:
    """IR情報のフィルタ・ソート・選定を行うクラス"""
    
    def extract_ir_info(self, wp_post):
        """WordPress投稿からIR情報を抽出"""
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
    
    def sort_by_priority(self, ir_list):
        """カテゴリ優先順位 + 重要度でソート"""
        return sorted(ir_list, key=lambda x: (
            get_category_priority(x['ir_type']),
            -get_importance_stars(x['importance'])
        ))
    
    def remove_duplicate_companies(self, ir_list):
        """同一企業の重複を排除（最優先IRのみ残す）"""
        seen_companies = set()
        result = []
        for ir in ir_list:
            stock_code = ir['stock_code']
            if stock_code and stock_code not in seen_companies:
                seen_companies.add(stock_code)
                result.append(ir)
        if len(ir_list) != len(result):
            print(f"🔄 重複排除: {len(ir_list)}件 → {len(result)}件")
        return result
    
    def select_top_n(self, ir_list, n=5):
        """Top N選定（最大5件）"""
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

    def _x_weighted_length(self, text):
        """Xの280文字制限に近い簡易カウント"""
        url_pattern = re.compile(r'https?://\S+|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/\S*)?')
        total = 0
        last_end = 0

        for match in url_pattern.finditer(text):
            total += self._weighted_plain_text_length(text[last_end:match.start()])
            total += 23
            last_end = match.end()

        total += self._weighted_plain_text_length(text[last_end:])
        return total

    def _weighted_plain_text_length(self, text):
        return sum(2 if ord(char) > 0x1100 else 1 for char in text)

    def _generate_single_company_tweet(self, ir, date_str, rank):
        """無料プランでも投稿できる短文テンプレートを生成"""
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        formatted_date = date_obj.strftime('%b %d, %Y')
        stock_code = ir['stock_code']
        company_name = ir['company_name']
        ir_type = ir['ir_type'].replace('_', ' ').title()

        company_line = f"{company_name} ({stock_code})" if stock_code else company_name

        variants = [
            [
                f"Japan IR Highlight {rank}/5 - {formatted_date}",
                "",
                company_line,
                ir_type,
                "",
                "See the key update in the image.",
                "",
                "japanir.jp/en",
                "#JapanStocks #IR",
            ],
            [
                f"Japan IR Highlight {rank}/5 - {formatted_date}",
                "",
                company_line,
                ir_type,
                "",
                "japanir.jp/en",
                "#JapanStocks #IR",
            ],
            [
                f"IR Highlight {rank}/5 - {formatted_date}",
                "",
                company_line,
                ir_type,
                "",
                "japanir.jp/en",
                "#JapanStocks #IR",
            ],
            [
                f"IR Highlight {rank}/5 - {formatted_date}",
                "",
                f"{stock_code} - {ir_type}" if stock_code else ir_type,
                "",
                "japanir.jp/en",
                "#JapanStocks #IR",
            ],
        ]

        for lines in variants:
            tweet = "\n".join(lines)
            if self._x_weighted_length(tweet) <= 280:
                return tweet

        return "\n".join(variants[-1])
    
    def generate_tweet(self, ir_list, date_str, rank=None):
        """X投稿文を生成"""
        if rank and ir_list:
            tweet = self._generate_single_company_tweet(ir_list[0], date_str, rank)
            print(f"📝 投稿文生成完了: {len(tweet)}文字 / X換算 {self._x_weighted_length(tweet)}/280")
            return tweet

        date_obj = datetime.strptime(date_str, '%Y%m%d')
        formatted_date = date_obj.strftime('%b %d, %Y')
        
        if rank:
            lines = [f"🇯🇵 Japan IR Highlight {rank}/5 - {formatted_date}", ""]
        else:
            lines = [f"🇯🇵 Japan IR Highlights - {formatted_date}", ""]
        
        for ir in ir_list:
            stock_code = ir['stock_code']
            company_name = ir['company_name']
            ir_type = ir['ir_type'].replace('_', ' ').title()
            summary = ir['short_summary']
            
            line = f"✅ {company_name} ({stock_code}) - {ir_type}"
            lines.append(line)
            lines.append("")
            lines.append(summary)
            lines.append("")
        
        lines.append("📊 Full analysis: japanir.jp/en")
        lines.append("")
        lines.append("#JapanStocks #IR")
        
        tweet = "\n".join(lines)
        
        if len(tweet) > self.max_length:
            print(f"⚠️ 投稿文が長すぎます: {len(tweet)}文字")
            tweet = tweet[:self.max_length - 100] + "\n\n...\n\n📊 japanir.jp/en\n\n#JapanStocks #IR"
        
        print(f"📝 投稿文生成完了: {len(tweet)}文字")
        return tweet


# ============================================================
# X投稿クラス（画像添付対応）
# ============================================================

class TwitterPoster:
    """Xに投稿するクラス（画像添付対応）"""
    
    def __init__(self, api_key, api_secret, access_token, access_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.access_secret = access_secret
    
    def upload_media(self, image_path):
        """
        画像をアップロード（tweepy v1 API使用）
        
        Args:
            image_path: 画像ファイルのパス
        
        Returns:
            str: media_id
        """
        try:
            # tweepy v1 API（Media Upload用）
            auth = tweepy.OAuth1UserHandler(
                self.api_key,
                self.api_secret,
                self.access_token,
                self.access_secret
            )
            api = tweepy.API(auth)
            
            # 画像アップロード
            media = api.media_upload(image_path)
            print(f"✅ 画像アップロード成功: media_id={media.media_id}")
            
            return str(media.media_id)
        
        except Exception as e:
            print(f"❌ 画像アップロードエラー: {e}")
            return None
    
    def already_posted_today(self, date_str, rank=None):
        """同日の投稿が既に存在するか確認"""
        try:
            client = tweepy.Client(
                consumer_key=self.api_key,
                consumer_secret=self.api_secret,
                access_token=self.access_token,
                access_token_secret=self.access_secret
            )
            me = client.get_me()
            if not me or not me.data:
                return False

            date_obj = datetime.strptime(date_str, '%Y%m%d')
            date_label = date_obj.strftime('%b %d, %Y')  # "Jun 08, 2026"
            marker = f"Japan IR Highlight {rank}/5 - {date_label}" if rank else date_label

            tweets = client.get_users_tweets(
                id=me.data.id,
                max_results=10,
                tweet_fields=['text', 'created_at']
            )
            if tweets and tweets.data:
                for tweet in tweets.data:
                    if marker in tweet.text:
                        print(f"⚠️ 投稿済み（{marker}）: ID {tweet.id}")
                        return True
            return False

        except Exception as e:
            print(f"⚠️ 重複チェックエラー（スキップして続行）: {e}")
            return False

    def post(self, tweet_text, image_path=None):
        """
        Xに投稿（画像添付オプション）

        Args:
            tweet_text: 投稿文
            image_path: 画像ファイルのパス（省略可）

        Returns:
            bool: 成功/失敗
        """
        try:
            print(f"🐦 X投稿実行中...")
            
            # 画像アップロード
            media_ids = None
            if image_path and os.path.exists(image_path):
                print(f"📸 画像添付: {image_path}")
                media_id = self.upload_media(image_path)
                if media_id:
                    media_ids = [media_id]
            
            # tweepy v2 Client初期化
            client = tweepy.Client(
                consumer_key=self.api_key,
                consumer_secret=self.api_secret,
                access_token=self.access_token,
                access_token_secret=self.access_secret
            )
            
            # 投稿実行
            if media_ids:
                response = client.create_tweet(text=tweet_text, media_ids=media_ids)
            else:
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
        """エラー通知メールを送信"""
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

def main(date_str, time_start, time_end, image_path=None, dry_run=False, rank=None):
    """
    メイン処理
    
    Args:
        date_str: 日付（YYYYMMDD）
        time_start: 開始時刻（HH:MM）
        time_end: 終了時刻（HH:MM）
        image_path: 画像ファイルのパス（省略可）
        dry_run: テストモード
        rank: TOP5内の投稿順位（1〜5、省略時は従来どおりTOP5まとめ）
    
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
    if rank:
        print(f"投稿順位: {rank}")
    if image_path:
        print(f"画像: {image_path}")
    print("")
    
    # WordPress APIからデータ取得
    fetcher = WordPressIRFetcher()
    wp_posts = fetcher.fetch_irs(date_str, time_start, time_end)
    
    if len(wp_posts) == 0:
        print("ℹ️ 該当するIR情報がありませんでした")
        return True
    
    # IR情報処理
    processor = IRDataProcessor()
    ir_list = [processor.extract_ir_info(post) for post in wp_posts]
    
    print(f"📊 全件対象: {len(ir_list)}件")
    
    ir_list = processor.sort_by_priority(ir_list)
    ir_list = processor.remove_duplicate_companies(ir_list)
    ir_list = processor.select_top_n(ir_list, 5)

    if rank:
        if len(ir_list) < rank:
            print(f"ℹ️ TOP5内のrank {rank}に該当するIR情報がありませんでした")
            return True
        selected_ir = ir_list[rank - 1]
        print(f"🎯 投稿対象: {selected_ir.get('company_name', '')} ({selected_ir.get('stock_code', '')})")
        ir_list = [selected_ir]
    
    # 投稿文生成
    generator = TweetGenerator()
    tweet_text = generator.generate_tweet(ir_list, date_str, rank=rank)
    
    print("")
    print("=" * 60)
    print("📄 生成された投稿文:")
    print("=" * 60)
    print(tweet_text)
    print("=" * 60)
    print("")
    
    # X投稿実行
    if dry_run:
        print("🧪 テストモード: X投稿をスキップします")
        if image_path and os.path.exists(image_path):
            print(f"📸 画像確認: {image_path} は存在します")
        print("")
        print("=" * 60)
        print("✅ テスト完了（投稿文のみ生成）")
        print("=" * 60)
        return True
    
    # API認証情報
    api_key = os.getenv('X_API_KEY')
    api_secret = os.getenv('X_API_SECRET')
    access_token = os.getenv('X_ACCESS_TOKEN')
    access_secret = os.getenv('X_ACCESS_SECRET')
    
    if not all([api_key, api_secret, access_token, access_secret]):
        print("⚠️ X API認証情報が設定されていません（環境変数）")
        return True
    
    # 投稿実行
    poster = TwitterPoster(api_key, api_secret, access_token, access_secret)

    # 重複チェック
    if poster.already_posted_today(date_str, rank=rank):
        print("✅ 対象投稿は投稿済みのためスキップします")
        return True

    success = poster.post(tweet_text, image_path)
    
    return success


def main_with_retry(date_str, time_start, time_end, image_path=None, dry_run=False, max_retries=3, rank=None):
    """メイン処理（リトライ付き）"""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"\n📝 試行 {attempt}/{max_retries}")
            
            success = main(date_str, time_start, time_end, image_path, dry_run, rank)
            
            if success:
                print("\n" + "=" * 60)
                print("✅ 処理が正常に完了しました")
                print("=" * 60)
                return True
            
        except Exception as e:
            error_msg = f"試行 {attempt} 失敗: {str(e)}"
            print(f"\n❌ {error_msg}")
            
            if attempt == max_retries:
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
            
            print(f"⏳ 10秒待機してリトライします...")
            time.sleep(10)
    
    return False


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='WordPress REST APIからIR情報を取得してX投稿（画像添付対応）'
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
        '--image-path',
        help='X投稿に添付する画像ファイルのパス（省略可）'
    )
    parser.add_argument(
        '--rank',
        type=int,
        choices=range(1, 6),
        metavar='N',
        help='投稿順位（1〜5、省略時は従来どおりTOP5まとめ）'
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
    
    if args.rank and not args.image_path:
        args.image_path = f"japan_ir_single_{args.date}_rank{args.rank}.png"

    # 画像ファイル確認
    if args.image_path and not os.path.exists(args.image_path):
        print(f"⚠️ 警告: 画像ファイルが見つかりません: {args.image_path}")
        print("画像なしで続行します")
        args.image_path = None
    
    # メイン処理実行
    success = main_with_retry(
        args.date, 
        args.time_start, 
        args.time_end,
        args.image_path,
        dry_run=args.dry_run,
        rank=args.rank
    )
    
    sys.exit(0 if success else 1)
