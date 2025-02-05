
from __future__ import annotations
from typing import List, Dict, Optional, Literal, TypedDict, Callable
import datetime
import uuid
import re
import logging
import firebase_admin
from firebase_admin import credentials, auth, firestore
import firebase_admin.exceptions

logging.basicConfig(filename="syftset_backend.log", 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filemode='w')
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

cred = credentials.Certificate("syftset1-firebase-adminsdk-dzdch-cb281d2bac.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Define the AccountType type
AccountType = Literal["main", "crypto-1", "forex-1"]
TransactionType = Literal["deposit", "withdrawal", "trading_outcome", "referral_bonus", "upline_commission",
                          "management_fee", "trading_fee"]
class AccountSessionData(TypedDict, total=False):  
    session_id: str
    starting_balance: float
    pnl: float
    trading_fee: float
    referral_bonus: float
    upline_commission: float

class User:
    """
    Represents a user in the system.
    """
    def __init__(self, name: str, email: str, id: Optional[str] = None, referred_by: Optional[str] = None,
                referrals: Optional[List[str]] = None, timestamp: Optional[datetime.datetime] = None):
        self.id = id or str(uuid.uuid4())
        self.name = name
        self.email = email
        self.referred_by = referred_by # User who referred this user(Id)
        self.referrals = referrals or [] # Users referred by this user(Ids)
        self.timestamp = timestamp or datetime.datetime.now(datetime.timezone.utc)

    def to_dict(self) -> Dict:
        """Serializes the user instance to a dictionary."""
        return {
            "name": self.name,
            "email": self.email,
            "id": self.id,
            "referred_by": self.referred_by,
            "referrals": self.referrals,
            "timestamp": self.timestamp
        }
        
    @staticmethod
    def from_dict(source: Dict) -> User:
        """Deserializes a dictionary into a User instance."""
        return User(
            name=source["name"],
            email=source["email"],
            id=source["id"],
            referred_by=source["referred_by"],
            referrals=source["referrals"],
            timestamp=source["timestamp"]
        )

    @staticmethod
    def retrieve_user_from_firestore(user_id: str) -> User:
        """Retrieves a user from Firestore using their ID."""
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()
        if not user_doc.exists:
            raise ValueError(f"User with ID {user_id} not found.")
        return User.from_dict(user_doc.to_dict())
    
    def save_to_firestore(self):
        """Saves the user instance to Firestore."""
        user_ref = db.collection("users").document(self.id)
        user_data = self.to_dict()
        
        user_ref.set(user_data, merge=True)
        logging.info(f"User {self.name} saved successfully to Firestore.")

    def update_firestore_details(self, updates: Dict):
        """Updates specific fields for the user in Firestore."""
        # ToDo: use this for firestore updates
        user_ref = db.collection("users").document(self.id)
        
        user_ref.update(updates)
        logging.info(f"User {self.name} updated successfully in Firestore.")


    def register(self, password: str):    
        """
        Registers a new user in Firebase Authentication.
        """
        try:
            auth.create_user(uid=self.id, email=self.email, password=password, display_name=self.name)
            self.save_to_firestore()
            logging.info(f"User {self.name} registered successfully.")
        except firebase_admin.exceptions.FirebaseError as e:
            logging.error(f"Error registering user {self.name}: {e}")
            return None

    def get_trading_account_from_firestore(self, account_type: AccountType) -> Optional[Account]:
        """Retrieves a trading account for the user from Firestore."""
        try:
            return Account.retrieve_account_from_firestore(self.id, account_type)
        except ValueError as e:
            logging.warning(f"No trading account found for user {self.name}: {e}")
            return None
    
    def create_trading_account(self, account_type: AccountType, initial_deposit: float = 0.0, management_fee_pct: float = 0.02,
            trading_fee_pct: float = 0.25, upline_commission_pct=0.05, timestamp: Optional[datetime.datetime] = None) -> Account:
        """
        Creates a new trading account for the user with an initial deposit.
        """
        account = Account(self.id, account_type, management_fee_pct=management_fee_pct, \
                          trading_fee_pct=trading_fee_pct, upline_commission_pct=upline_commission_pct,
                         timestamp=timestamp)
        if initial_deposit:
            account.deposit(initial_deposit, timestamp=timestamp)
        account.save_to_firestore()
        logging.info(f"Trading account {account_type} created for user {self.name}.")
        return account
    
    def refer(self, name: str, email: str, timestamp: Optional[datetime.datetime] = None) -> User:
        """
        Refers a new user, creates the referred user instance, and updates referrals list.

        Returns the referred user instance
        """
        referred_user = User(name=name, email=email, referred_by=self.id, timestamp=timestamp)
        self.referrals.append(referred_user.id)
        self.update_firestore_details({"referrals": self.referrals})
        referred_user.save_to_firestore()
        logging.info(f"User {self.name} referred {referred_user.name}.")
        return referred_user 
     
class Account:
    """
    Represents a trading account for a user.
    """
    def __init__(self, user_id: str, account_type: AccountType, id: Optional[str] = None, balance: float = 0.0,
            management_fee_pct: float = 0.02, trading_fee_pct: float = 0.25, total_deposits: float = 0.0,
            total_withdrawals: float = 0.0, total_pnl: float = 0.0, total_trading_fee: float = 0.0, 
            total_management_fee: float = 0.0, recent_activities: Optional[List[Dict]] = None, 
            total_referral_earnings: float = 0.0, total_upline_commission: float = 0.0, 
            upline_commission_pct: float = 0.05, can_receive_referral_bonus: bool = True,
            can_yield_referral_bonus: bool = True, referral_earnings: float = 0.0,
            timestamp: Optional[datetime.datetime] = None,
        ):
        self.user_id = user_id
        self.account_type = account_type
        self.id = id or str(uuid.uuid4())
        self.balance = balance
        self.management_fee_pct = management_fee_pct
        self.trading_fee_pct = trading_fee_pct
        self.upline_commission_pct = upline_commission_pct # pct of trading profits allocated to User who referred this user
        self.total_deposits = total_deposits
        self.total_withdrawals = total_withdrawals
        self.total_pnl = total_pnl
        self.total_trading_fee = total_trading_fee
        self.total_management_fee = total_management_fee
        self.recent_activities = recent_activities or []
        self.can_receive_referral_bonus = can_receive_referral_bonus # to check if a user can receive referral bonuse
        self.can_yield_referral_bonus = can_yield_referral_bonus # to check if user can give upline bonus from profits
        self.referral_earnings = referral_earnings
        self.total_referral_earnings = total_referral_earnings # Earnings from Users referred by this User
        self.total_upline_commission = total_upline_commission # Earnings to User who referred this user 
        self.timestamp = timestamp or datetime.datetime.now(datetime.timezone.utc)

    def to_dict(self) -> Dict:
        """Serializes the account instance to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "account_type": self.account_type,
            "balance": self.balance,
            "management_fee_pct": self.management_fee_pct,
            "trading_fee_pct": self.trading_fee_pct,
            "total_deposits": self.total_deposits,
            "total_withdrawals": self.total_withdrawals,
            "total_pnl": self.total_pnl,
            "total_trading_fee": self.total_trading_fee,
            "total_management_fee": self.total_management_fee,
            "recent_activities": self.recent_activities,
            "total_referral_earnings": self.total_referral_earnings,
            "total_upline_commission": self.total_upline_commission,
            "upline_commission_pct": self.upline_commission_pct,
            "referral_earnings": self.referral_earnings,
            "can_receive_referral_bonus": self.can_receive_referral_bonus,
            "can_yield_referral_bonus": self.can_yield_referral_bonus,
            "timestamp": self.timestamp
        }

    @staticmethod
    def from_dict(source: Dict) -> Account:
        """Deserializes a dictionary into an Account instance."""
        return Account(
            user_id=source["user_id"],
            account_type=source["account_type"],
            id=source["id"],
            balance=source["balance"],
            management_fee_pct=source["management_fee_pct"],
            trading_fee_pct=source["trading_fee_pct"],
            total_deposits=source["total_deposits"],
            total_withdrawals=source["total_withdrawals"],
            total_pnl=source["total_pnl"],
            total_trading_fee=source["total_trading_fee"],
            total_management_fee=source["total_management_fee"],
            recent_activities=source["recent_activities"],
            total_referral_earnings=source["total_referral_earnings"],
            total_upline_commission=source["total_upline_commission"],
            upline_commission_pct=source["upline_commission_pct"],
            referral_earnings=source["referral_earnings"],
            can_receive_referral_bonus=source["can_receive_referral_bonus"],
            can_yield_referral_bonus=source["can_yield_referral_bonus"], 
            timestamp=source["timestamp"]
        )
    
    @staticmethod
    def retrieve_account_from_firestore(user_id: str, account_type: AccountType) -> Account:
        """Retrieves a user's account from Firestore using their ID"""
        account_ref = db.collection("users").document(user_id).collection("accounts").document(account_type)
        account_doc = account_ref.get()
        if not account_doc.exists:
            raise ValueError(f"Account - {account_type} - of user - {user_id} - not found.")
        return Account.from_dict(account_doc.to_dict())

    def save_to_firestore(self) -> None:
        """Saves the account to Firestore."""
        account_ref = db.collection("users").document(self.user_id).collection("accounts").document(self.account_type)

        account_data = self.to_dict()
        
        account_ref.set(account_data, merge=True)
        logging.info(f"Account {self.account_type} for user {self.user_id} saved successfully.")

    def update_firestore_details(self, updates: Dict) -> None:
        """Updates specific fields for the account in Firestore."""
        account_ref = db.collection("users").document(self.user_id).collection("accounts").document(self.account_type)

        account_ref.update(updates)
        logging.info(f"Account - {self.account_type} - for user - {self.user_id} - updated successfully.")

    def update_recent_activities(self, activity: Dict) -> None:
        """Updates the recent activities for the account."""
        max_recent_activities = 20
        
        # Trim recent activities list if it exceeds the limit
        if len(self.recent_activities) >= (max_recent_activities):
            self.recent_activities.pop()
        
        self.recent_activities.insert(0, activity)
        
    def deposit(self, amount: float, description: Optional[str]=None, timestamp: Optional[datetime.datetime] = None):
        """
        Handles deposits to the account, updates balance, and logs a transaction.
        """
        description = description or f"Made a ${amount} deposit."
        new_balance = self.balance + amount

        # Add Transaction to firestore
        transaction = Transaction.process_transaction(self.user_id, self.account_type, transaction_type="deposit", amount=amount, prev_balance=self.balance,
                                  new_balance=new_balance, description=description, timestamp=timestamp)

        self.update_recent_activities(transaction.to_activity())
        self.balance = new_balance
        self.total_deposits += amount

        # save current account details to firestore
        self.save_to_firestore()

    def withdraw(self, amount: float, timestamp: Optional[datetime.datetime] = None):
        """
        Handles withdrawals from the account, updates balance, and logs a transaction.
        """
        if self.balance < amount:
            raise ValueError(f"Withdrawal amount of ${amount} exceeds account balance of ${self.balance}")
            
        description = f"Made a ${amount} withdrawal."
        new_balance = self.balance - amount

        # Add Transaction to firestore
        transaction = Transaction.process_transaction(self.user_id, self.account_type, transaction_type="withdrawal", amount=amount, prev_balance=self.balance,
                                  new_balance=new_balance, description=description, timestamp=timestamp)

        self.update_recent_activities(transaction.to_activity())
        self.balance = new_balance
        self.total_withdrawals += amount

        # save current account details to firestore
        self.save_to_firestore()

    def withdraw_from_referral_bonus(self, amount: float, timestamp: Optional[datetime.datetime] = None):
        """
        Handles withdrawals from the referral bonus balance and logs a transaction.
        """
        if self.referral_earnings < amount:
            raise ValueError(f"Withdrawal amount of ${amount} exceeds referral bonus balance of ${self.referral_earnings}")
            
        description = f"Made a ${amount} withdrawal from referral bonus balance."
        new_balance = self.referral_earnings - amount

        # Add Transaction to firestore
        transaction = Transaction.process_transaction(self.user_id, self.account_type, transaction_type="withdrawal", amount=amount, prev_balance=self.referral_earnings,
                                  new_balance=new_balance, description=description, timestamp=timestamp)

        self.update_recent_activities(transaction.to_activity())
        self.referral_earnings = new_balance

        # ToDo: Decide if withdrawal from referral bonus should affect total withdrawals
        self.total_withdrawals += amount

        # save current account details to firestore
        self.save_to_firestore()

    def close_account(self, timestamp: Optional[datetime.datetime] = None):
        """
        Closes the account by withdrawing the entire balance.
        """
        amount = self.balance
        self.withdraw(amount, timestamp=timestamp)

    def get_referrer_account(self, referrer: User, check_bonus_eligibility: bool = True) -> Optional[Account]:
        """
        Retrieves or creates the trading account of the same account type for the referrer and validates bonus eligibility. 
        """
        if not referrer:
            return None

        # Approach chosen here is that Referral Profits Go to Account instance, not user instance. If user A refers user B who opens account type A, bonus from
        # this account type A will go to user A's account A. If user A has no account A, it should be created for them.
        # This introduces users to different account types, which could lead to more engagement.
        referrer_account = referrer.get_trading_account_from_firestore(self.account_type) or referrer.create_trading_account(self.account_type)

        if check_bonus_eligibility:
            return referrer_account if self.can_yield_referral_bonus and referrer_account.can_receive_referral_bonus else None

        return referrer_account
    
    def apply_referral_bonus(self, referrer_account: Account, upline_commission: float, user_name: str, referrer_name: str, session_number: int, session_id: str, timestamp: datetime.datetime):
        """
        Applies the referral bonus to the referrer's account, logs the transaction,
        and updates the referrer's earnings.
        """
        # Log the referral bonus for the referrer
        description = f"Session {session_number}: ${upline_commission} referral bonus from {user_name}"
        new_balance = referrer_account.total_referral_earnings + upline_commission
        transaction = Transaction.process_transaction(
            user_id=referrer_account.user_id,
            account_type=referrer_account.account_type,
            transaction_type="referral_bonus",
            amount=upline_commission,
            prev_balance=referrer_account.total_referral_earnings,
            new_balance=new_balance,
            description=description,
            timestamp=timestamp
        )
        referrer_account.update_recent_activities(transaction.to_activity())
        referrer_account.total_referral_earnings += upline_commission
        referrer_account.referral_earnings += upline_commission
        referrer_account.save_to_firestore()

        # Log referrer's session records.
        referrer_session_details = AccountSessionDetails(session_number, referrer_account.account_type, referrer_account.user_id, timestamp=timestamp)
        referrer_session_details.update_session_performance_records(referral_bonus=upline_commission, starting_balance=referrer_account.balance)

        # Log the upline commission for the current user
        upline_description = f"Session {session_number}: ${upline_commission} upline commission to {referrer_name}"
        new_upline_balance = self.total_upline_commission + upline_commission
        upline_transaction = Transaction.process_transaction(
            user_id=self.user_id,
            account_type=self.account_type,
            transaction_type="upline_commission",
            amount=upline_commission,
            prev_balance=self.total_upline_commission,
            new_balance=new_upline_balance,
            id=session_id,
            description=upline_description,
            timestamp=timestamp
        )
        self.update_recent_activities(upline_transaction.to_activity())
        self.total_upline_commission += upline_commission

    def calculate_fees_and_commissions(self, gross_pnl: float, referrer_account: Account):
        """
        Calculates trading fee and upline commission based on the gross profit and referrer eligibility.
        """
        trading_fee = gross_pnl * self.trading_fee_pct
        upline_commission = gross_pnl * self.upline_commission_pct if referrer_account else 0
        return trading_fee, upline_commission

    def update_performance_metrics(self, session_number: int, session_id: str, net_pnl: float, trading_fee: float, timestamp: datetime.datetime):
        """
        Updates the performance metrics and logs transactions for trading fees and session outcomes.
        """
        # Update session performance
        session_description = f"Session {session_number}'s return on investment: {'+' if net_pnl > 0 else '-'}${abs(net_pnl)}"
        roi_transaction = Transaction.process_transaction(
            user_id=self.user_id,
            account_type=self.account_type,
            transaction_type="trading_outcome",
            amount=net_pnl,
            prev_balance=self.balance,
            new_balance=self.balance + net_pnl,
            id=session_id,
            description=session_description,
            timestamp=timestamp
        )
        self.update_recent_activities(roi_transaction.to_activity())
        self.balance += net_pnl
        self.total_pnl += net_pnl

        if trading_fee:
            # Update trading fee
            fee_description = f"Session {session_number}'s trading fee: ${trading_fee}"
            trading_fee_transaction = Transaction.process_transaction(
                user_id=self.user_id,
                account_type=self.account_type,
                transaction_type="trading_fee",
                amount=trading_fee,
                prev_balance=self.total_trading_fee,
                new_balance=self.total_trading_fee + trading_fee,
                id=session_id,
                description=fee_description,
                timestamp=timestamp
            )
            self.update_recent_activities(trading_fee_transaction.to_activity())
            self.total_trading_fee += trading_fee

    def distribute_profit_split(self, profit_percentage: float, session_number: int, user: Optional[User] = None, referrer: Optional[User] = None, timestamp: Optional[datetime.datetime]=None, get_account: Optional[Callable[[str], Optional[Account]]]=None):
        """
        Main method to calculate the profit split, update balances, and handle referral bonuses.
        """
        gross_pnl = self.balance * profit_percentage
        net_pnl = gross_pnl
        trading_fee = 0
        upline_commission = 0
        session_id = f"session_{session_number}"

        if profit_percentage > 0:
            referrer_account = self.get_referrer_account(referrer)
            
            # use referrer_account local instance if existing. This is to prevent unintended overwrites.
            if referrer_account and get_account:
                referrer_account = get_account(referrer_account.id) or referrer_account

            trading_fee, upline_commission = self.calculate_fees_and_commissions(gross_pnl, referrer_account)
            net_pnl = gross_pnl - trading_fee - upline_commission

            if referrer_account:
                self.apply_referral_bonus(referrer_account, upline_commission, user.name, referrer.name, session_number, session_id, timestamp)

        # update session_records before updating performance record. This is to capture starting balance before it is incremented
        session_details = AccountSessionDetails(session_number, self.account_type, self.user_id, timestamp=timestamp)
        session_details.update_session_performance_records(trading_fee=trading_fee, 
            upline_commission=upline_commission, pnl=net_pnl, starting_balance=self.balance)
        
        # update performance metrics
        self.update_performance_metrics(session_number, session_id, net_pnl, trading_fee, timestamp)
        self.save_to_firestore()

    def charge_management_fee(self, timestamp: Optional[datetime.datetime] = None) -> None:
        """
        Charges a management fee based on the account balance, updates metrics,
        and logs the transaction to Firestore.
        """
        management_fee = self.management_fee_pct * self.balance
        fee_description = f"Management fee deducted: ${management_fee}"
        management_fee_transaction = Transaction.process_transaction(
            user_id=self.user_id,
            account_type=self.account_type,
            transaction_type="management_fee",
            amount=management_fee,
            prev_balance=self.total_management_fee,
            new_balance=self.total_management_fee + management_fee,
            description=fee_description,
            timestamp=timestamp
        )
        self.update_recent_activities(management_fee_transaction.to_activity())
        self.balance -= management_fee
        self.total_management_fee += management_fee
        self.save_to_firestore()

class Transaction:
    """
    Tracks account transactions like deposits, withdrawals, profits, and bonuses.
    """
    def __init__(self, user_id: str, account_type: AccountType, transaction_type: TransactionType, amount: float, prev_balance: float,
            new_balance: float, id: Optional[str] = None, description: str = "", timestamp: Optional[datetime.datetime] = None,
        ):
        self.id = id or str(uuid.uuid4())
        self.user_id = user_id
        self.account_type = account_type
        self.transaction_type = transaction_type 
        self.amount = amount
        self.prev_balance = prev_balance
        self.new_balance = new_balance
        self.timestamp = timestamp or datetime.datetime.now(datetime.timezone.utc)
        self.description = self._process_description(description)

    def to_dict(self) -> Dict:
        """Serializes the transaction to a dictionary"""
        return {
            "id": self.id,
            "transaction_type": self.transaction_type,
            "amount": self.amount,
            "timestamp": self.timestamp,
            "prev_balance": self.prev_balance,
            "new_balance": self.new_balance,
            "description": self.description
        }
    
    def to_activity(self) -> Dict:
        """Converts the transaction into a simplified activity log."""
        return {
            "id": self.id,
            "activity_type": self.transaction_type,
            "description": self.description,
            "timestamp": self.timestamp,
        }
    
    def save_to_firestore(self) -> None:
        """
        Saves the transaction to Firestore under the user's account transactions collection.
        """
        transaction_ref = db.collection("users").document(self.user_id).collection(
            self.transaction_type).document(self.account_type).collection("entries").document(self.id)
        transactions_data = self.to_dict()
        transaction_ref.set(transactions_data, merge=True)
        logging.info(f"Transaction Added successfully. \nDetails:\nTransaction Type: {self.transaction_type}\nAmount: {self.amount}\
        \nUser Id: {self.user_id}\nAccount Type: {self.account_type}")

    @staticmethod
    def process_transaction(user_id: str, account_type: AccountType, transaction_type: TransactionType, amount: float, prev_balance: float,
            new_balance: float, id: Optional[str] = None, description: str = "", timestamp: Optional[datetime.datetime] = None,
        ) -> Transaction:
        """
        Creates and saves a transaction to Firebase.
        """
        transaction = Transaction(
            user_id=user_id,
            account_type=account_type,
            transaction_type=transaction_type,
            amount=amount,
            prev_balance=prev_balance,
            new_balance=new_balance,
            id=id,
            description=description,
            timestamp=timestamp
        )
        transaction.save_to_firestore()
        return transaction
    
    @staticmethod
    def _process_description(description: str) -> str:
        """
        Processes the description by rounding any numbers (with or without $) to 2 decimal places.
        Ensures the negative or positive sign is properly placed with the dollar sign.
        """
        if not description:
            return ""

        # Match numbers with optional +/-, $ and decimals
        pattern = r"([-+]?\$?\d+\.\d+)"

        def round_match(match):
            original = match.group(1)  # E.g., "$1.2338949" or "-$1.2338949"
            has_dollar = "$" in original  # Check if the number includes a dollar sign
            sign = "-" if "-" in original else "+" if "+" in original else ""  # Capture the sign
            number = float(original.replace("$", "").replace("-", "").replace("+", ""))  # Strip $ and signs
            rounded = round(number, 2)  # Round to 2 decimal places
            return f"{sign}${rounded:.2f}" if has_dollar else f"{sign}{rounded:.2f}"  # Place the sign before $

        return re.sub(pattern, round_match, description)

class TradingSession:
    def __init__(self, account_type: AccountType, profit_percentage: float, session_number: int, 
                 start_date: datetime.datetime, end_date: datetime.datetime, 
                 btc_percentage_change: Optional[float] = None, eth_percentage_change: Optional[float] = None):
        self.users: List[User] = []
        self.accounts: List[Account] = []
        self.account_type = account_type
        self.profit_percentage = profit_percentage
        self.session_number = session_number
        self.id = f"session_{session_number}"
        self.start_date = start_date
        self.end_date = end_date
        self.btc_percentage_change = btc_percentage_change # btc movement within the session. To compare against profit_percentage. Useful when account type if crypto-based
        self.eth_percentage_change = eth_percentage_change
        
    def to_dict(self) -> Dict:
        """Serializes the TradingSession instance to a dictionary."""
        return {
            "id": self.id,
            "account_type": self.account_type,
            "profit_percentage": self.profit_percentage,
            "session_number": self.session_number,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "btc_percentage_change": self.btc_percentage_change,
            "eth_percentage_change": self.eth_percentage_change
        }

    def populate_users_and_accounts(self) -> None:
        """
        Populates the list of users and their accounts for the specified account type.
        """
        # firestore db is structured in the /users/{user_id}/accounts/{account_type} format. You can use collection_group 
        # to get accounts first, then get users whose account have been added. However, since this is a hobby project, 
        # the implementation here serves.
        users_ref = db.collection("users") 
        users = users_ref.stream()
    
        for user_doc in users:
            user_id = user_doc.id
            user_data = user_doc.to_dict()
    
            # Get user's account
            account_ref = db.collection("users").document(user_id).collection("accounts").document(self.account_type)
            account_doc = account_ref.get()

            add_user = False
            if account_doc.exists:
                account_data = account_doc.to_dict()

                if account_data["balance"] > 0:
                    self.accounts.append(Account.from_dict(account_data))
                    add_user = True
    
            if add_user: # Add user only if user has account type specified. This should be handled using firebase queries or something.
                self.users.append(User.from_dict(user_data))

        logging.info("Users and accounts successfully populated")

    def get_user(self, user_id: str) -> Optional[User]:
        """
        Retrieves a user either from the local cache or from Firestore.
        """
        user = next((user for user in self.users if user.id == user_id), None)
        if not user:
            try:
                user = User.retrieve_user_from_firestore(user_id)
            except ValueError:
                pass
        return user
    
    def get_session_account(self, account_id: str) -> Optional[Account]:
        """
        Retrieves a account either from the local cache.
        """
        account = next((account for account in self.accounts if account.id == account_id), None)

        return account

    def credit_profits(self) -> None:
        """
        Credits profits to all accounts in the session.
        """    
        if not len(self.accounts):
            self.populate_users_and_accounts()
            
        for account in self.accounts:
            user = self.get_user(account.user_id)
            referrer = self.get_user(user.referred_by) if user.referred_by else None
            account.distribute_profit_split(self.profit_percentage, self.session_number, user, referrer, timestamp=self.end_date, get_account=self.get_session_account)

    def get_total_balance(self) -> float:
        """
        Calculates the total balance across all accounts in the session.
        """      
        if not len(self.accounts):
            self.populate_users_and_accounts()

        total_balance = 0
        for account_data in self.accounts:
            total_balance += account_data.balance
            
        return total_balance
    
    def save_to_firestore(self):
        """Saves the trading session instance to Firestore."""
        session_ref = db.collection("sessions").document(self.account_type).collection("entries").document(self.id)
        session_data = self.to_dict()
        
        session_ref.set(session_data, merge=True)
        logging.info(f"Session {self.session_number} details added successfully.")

class AccountSessionDetails:
    def __init__(self, session_number: int, account_type: AccountType, user_id: str, timestamp: Optional[datetime.datetime]=None):
        self.id = f"session_{session_number}"
        self.account_type = account_type
        self.user_id = user_id
        self.timestamp = timestamp or datetime.datetime.now(datetime.timezone.utc)

    def _get_session_ref(self):
        """
        Returns the Firestore reference for the current session.
        """
        return (
            db.collection("users")
            .document(self.user_id)
            .collection("sessions")
            .document(self.account_type)
            .collection("entries")
            .document(self.id)
        )
    
    def _get_existing_session_data(self, session_ref) -> Optional[AccountSessionData]:
        """
        Retrieves the existing session document data if it exists.
        """
        session_doc = session_ref.get()
        return session_doc.to_dict() if session_doc.exists else None
    
    def update_session_performance_records(
        self,
        trading_fee: float = 0.0,
        referral_bonus: float = 0.0,
        upline_commission: float = 0.0,
        pnl: float = 0.0,
        starting_balance: float = 0.0,
    ):
        """
        Updates session performance records in Firestore.
        If the session doesn't exist, it initializes a new record.
        """
        session_ref = self._get_session_ref()
        existing_data = self._get_existing_session_data(session_ref)

        if not existing_data:
            # Create a new session document
            session_data = {
                "id": self.id,
                "starting_balance": starting_balance,
                "pnl": pnl,
                "trading_fee": trading_fee,
                "referral_bonus": referral_bonus,
                "upline_commission": upline_commission,
                "timestamp": self.timestamp
            }
            logging.info(f"Creating new session document for user {self.user_id}, session {self.id}.")
        else:
            # Update an existing session document
            session_data = {
                "starting_balance": existing_data["starting_balance"],  # Shouldn't be updated
                "pnl": pnl or existing_data["pnl"],
                "trading_fee": trading_fee or existing_data["trading_fee"],
                "upline_commission": upline_commission or existing_data["upline_commission"],
                "referral_bonus": firestore.Increment(referral_bonus),  # Accumulates over time
            }
            logging.info(f"Updating existing session document for user {self.user_id}, session {self.id}.")

        session_ref.set(session_data, merge=True)
        logging.info(f"{self.account_type} session performance for user {self.user_id}, session {self.id} updated successfully.")
       
