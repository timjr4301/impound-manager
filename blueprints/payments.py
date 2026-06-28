"""
iPOSpays / CloudPOS payment integration for storage fee collection.
"""
import os
import json
import requests
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, Vehicle, PaymentTransaction, VehicleNote

bp = Blueprint('payments', __name__, url_prefix='/payments')

CLOUDPOS_API_URL = os.environ.get('CLOUDPOS_API_URL', 'https://api.iposservice.com/v1')
CLOUDPOS_TPN = os.environ.get('CLOUDPOS_TPN', '')
CLOUDPOS_MERCHANT_ID = os.environ.get('CLOUDPOS_MERCHANT_ID', '')
CLOUDPOS_API_KEY = os.environ.get('CLOUDPOS_API_KEY', '')


def _cloudpos_charge(amount_cents, description, reference):
    """
    Submit a charge to CloudPOS.
    Returns (transaction_id, error_message).
    In sandbox/unconfigured mode, simulates approval.
    """
    if not all([CLOUDPOS_TPN, CLOUDPOS_MERCHANT_ID, CLOUDPOS_API_KEY]):
        # Simulate for dev — return a fake transaction ID
        return f'SIMULATED-{datetime.utcnow().strftime("%Y%m%d%H%M%S")}', None

    try:
        payload = {
            'tpn': CLOUDPOS_TPN,
            'merchant_id': CLOUDPOS_MERCHANT_ID,
            'amount': amount_cents,
            'currency': 'USD',
            'description': description,
            'reference': reference,
        }
        headers = {
            'Authorization': f'Bearer {CLOUDPOS_API_KEY}',
            'Content-Type': 'application/json',
        }
        resp = requests.post(
            f'{CLOUDPOS_API_URL}/transactions',
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('transaction_id') or data.get('id'), None
    except requests.exceptions.RequestException as exc:
        return None, str(exc)
    except Exception as exc:
        return None, str(exc)


@bp.route('/vehicle/<int:vehicle_id>', methods=['GET', 'POST'])
@login_required
def collect_payment(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    from titlebot.storage import calculate_storage
    storage_days, storage_total, _ = calculate_storage(
        vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0
    )
    total_owed = (vehicle.tow_fee or 0) + storage_total
    already_paid = sum(p.amount for p in vehicle.payments)
    balance = max(0, total_owed - already_paid)

    if request.method == 'POST':
        amount_str = request.form.get('amount', '').strip()
        payment_type = request.form.get('payment_type', 'CARD').upper()
        notes = request.form.get('notes', '').strip()

        try:
            amount = float(amount_str)
        except (ValueError, TypeError):
            flash('Invalid amount.', 'danger')
            return redirect(url_for('payments.collect_payment', vehicle_id=vehicle_id))

        if amount <= 0:
            flash('Amount must be positive.', 'danger')
            return redirect(url_for('payments.collect_payment', vehicle_id=vehicle_id))

        amount_cents = int(round(amount * 100))
        description = f'Storage fee — {vehicle.display_name} ({vehicle.plate or vehicle.vin or ""})'
        reference = f'VEH-{vehicle.id}-{date.today().strftime("%Y%m%d")}'

        transaction_id = None
        if payment_type == 'CARD':
            transaction_id, err = _cloudpos_charge(amount_cents, description, reference)
            if err:
                flash(f'Payment processing error: {err}', 'danger')
                return redirect(url_for('payments.collect_payment', vehicle_id=vehicle_id))

        txn = PaymentTransaction(
            vehicle_id=vehicle.id,
            amount=amount,
            payment_type=payment_type,
            payment_date=datetime.utcnow(),
            reference_number=reference,
            cloudpos_transaction_id=transaction_id,
            notes=notes or None,
            processed_by=current_user.display_name or current_user.username,
        )
        db.session.add(txn)
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Payment received: ${amount:.2f} ({payment_type}). Ref: {transaction_id or reference}. {notes}',
            author=current_user.display_name or current_user.username,
            created_at=datetime.utcnow(),
        ))
        vehicle.storage_paid = (vehicle.storage_paid or 0) + amount
        vehicle.payment_date = date.today()
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'Payment of ${amount:.2f} recorded successfully. Txn: {transaction_id or reference}', 'success')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

    return render_template('payments/collect.html',
        vehicle=vehicle,
        storage_days=storage_days,
        storage_total=storage_total,
        total_owed=total_owed,
        already_paid=already_paid,
        balance=balance,
        today=date.today(),
        cloudpos_configured=bool(CLOUDPOS_TPN),
    )


@bp.route('/vehicle/<int:vehicle_id>/history')
@login_required
def payment_history(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    return render_template('payments/history.html',
        vehicle=vehicle,
        payments=vehicle.payments,
    )
