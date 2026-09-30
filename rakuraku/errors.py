"""楽楽自動化のエラー。code (安定した識別子) と日本語メッセージ (i18nキー) を持つ。"""
from __future__ import annotations

SERVICE_NAMES = {"seisan": "楽楽精算", "kintai": "楽楽勤怠"}


class RakuError(Exception):
    code = "RAKU_ERROR"
    user_message = "自動処理でエラーが発生しました。"

    def __init__(self, detail="", service=None, user_message=None, site_message=""):
        # type: (str, str | None, str | None, str) -> None
        self.detail = detail
        self.service = service
        # 楽楽側の画面に出た文言 (例: 「パスワードが正しくありません」)。利用者にそのまま見せ、
        # 「IDとパスワードの誤りか、ツールの不具合か」を画面だけで切り分けられるようにする。
        self.site_message = site_message or ""
        # 翻訳用に未加工テンプレート ({service} を含む) を保持する。
        self.template = user_message or type(self).user_message
        if user_message:
            self.user_message = user_message
        elif "{service}" in self.user_message:
            name = SERVICE_NAMES.get(service, service) if service else "楽楽精算・楽楽勤怠"
            self.user_message = self.user_message.format(service=name)
        super().__init__(detail or self.user_message)


class LoginFailed(RakuError):
    code = "LOGIN_FAILED"
    user_message = "{service}にログインできませんでした。IDとパスワードをご確認ください。"


class AccountLocked(RakuError):
    code = "ACCOUNT_LOCKED"
    user_message = "{service}のアカウントがロックされています。{service}の管理者にロック解除を依頼してください。"


class PasswordExpired(RakuError):
    code = "PASSWORD_EXPIRED"
    user_message = "{service}のパスワードの有効期限が切れています。{service}で変更してください。"


class AdditionalAuthRequired(RakuError):
    code = "ADDITIONAL_AUTH_REQUIRED"
    user_message = "{service}で追加の認証（二段階認証など）が求められたため、自動処理を中止しました。"


class SiteUnavailable(RakuError):
    code = "SITE_UNAVAILABLE"
    user_message = "{service}に接続できませんでした（メンテナンス中またはネットワークの問題）。時間をおいて再度お試しください。"


class SelectorNotFound(RakuError):
    code = "SELECTOR_NOT_FOUND"
    user_message = "{service}の画面構成が想定と異なるため、処理を続行できませんでした。管理者に連絡してください。"


class MonthNavigationFailed(RakuError):
    code = "MONTH_NAVIGATION_FAILED"
    user_message = "楽楽勤怠で出勤簿の対象月を切り替えできませんでした。"


class ExportFailed(RakuError):
    code = "EXPORT_FAILED"
    user_message = "{service}のエクスポートに失敗しました。時間をおいて再度お試しください。"


class DownloadTimeout(RakuError):
    code = "DOWNLOAD_TIMEOUT"
    user_message = "{service}のダウンロードが時間内に完了しませんでした。時間をおいて再度お試しください。"


class ForbiddenActionBlocked(RakuError):
    code = "FORBIDDEN_ACTION_BLOCKED"
    user_message = "安全のため処理を中止しました（承認・差戻しなどの操作を検出）。管理者に連絡してください。"


class NoDataFound(RakuError):
    code = "NO_DATA"
    user_message = "対象データがありませんでした。条件をご確認ください。"


class PlaywrightUnavailable(RakuError):
    code = "PLAYWRIGHT_UNAVAILABLE"
    user_message = (
        "この機能はサーバ側でブラウザを実行するため、有料プラン（Playwright対応の環境）が必要です。"
        "無料プランでは楽楽精算・楽楽勤怠へ接続できません。"
    )


class CheckSheetFailed(RakuError):
    code = "CHECKSHEET_FAILED"
    user_message = "チェックシートの作成中にエラーが発生しました。"

    def __init__(self, detail="", service=None, user_message=None, log="", site_message=""):
        # type: (str, str | None, str | None, str, str) -> None
        self.log = log  # 画面の「実行ログ」に出す (サーバ内部のパスは除去済み)
        super().__init__(detail, service=service, user_message=user_message, site_message=site_message)
