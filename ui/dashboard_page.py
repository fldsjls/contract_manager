# QPainter 用于绘制开票/收票折线图。
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import Database


class LineChartWidget(QWidget):
    # 绘制按日期汇总的开票和收票折线图。
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.records: list[dict] = []
        self.setMinimumHeight(260)

    # 设置图表数据并触发重绘。
    def set_records(self, records: list[dict]) -> None:
        self.records = records
        self.update()

    # 使用 QPainter 绘制坐标线、图例和两条折线。
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(56, 28, -24, -42)

        painter.setPen(QPen(QColor("#d9dee7"), 1))
        painter.drawRect(rect)

        painter.setPen(QColor("#667085"))
        painter.drawText(12, 20, "开票 / 收票趋势")

        self._draw_legend(painter)
        if not self.records:
            painter.drawText(rect, Qt.AlignCenter, "暂无记录数据")
            return

        max_amount = max(
            max(item["invoice_amount"], item["payment_amount"])
            for item in self.records
        )
        max_amount = max(max_amount, 1)

        for index in range(1, 4):
            y = rect.bottom() - rect.height() * index / 4
            painter.setPen(QPen(QColor("#edf0f5"), 1))
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        invoice_points = self._points(rect, "invoice_amount", max_amount)
        payment_points = self._points(rect, "payment_amount", max_amount)
        self._draw_line(painter, invoice_points, QColor("#2563eb"))
        self._draw_line(painter, payment_points, QColor("#14b8a6"))

        painter.setPen(QColor("#667085"))
        for index, item in enumerate(self.records):
            if len(self.records) > 6 and index % max(len(self.records) // 6, 1) != 0:
                continue
            x = self._x_at(rect, index)
            painter.drawText(int(x) - 32, rect.bottom() + 22, 64, 18, Qt.AlignCenter, item["record_date"][5:])

    # 绘制图例。
    def _draw_legend(self, painter: QPainter) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2563eb"))
        painter.drawRect(150, 12, 14, 8)
        painter.setBrush(QColor("#14b8a6"))
        painter.drawRect(220, 12, 14, 8)
        painter.setPen(QColor("#344054"))
        painter.drawText(168, 21, "开票")
        painter.drawText(238, 21, "收票")

    # 根据数据生成图表坐标点。
    def _points(self, rect, key: str, max_amount: float) -> list[QPointF]:
        points = []
        for index, item in enumerate(self.records):
            x = self._x_at(rect, index)
            ratio = item[key] / max_amount
            y = rect.bottom() - rect.height() * ratio
            points.append(QPointF(x, y))
        return points

    # 根据序号计算 X 坐标。
    def _x_at(self, rect, index: int) -> float:
        if len(self.records) == 1:
            return rect.center().x()
        return rect.left() + rect.width() * index / (len(self.records) - 1)

    # 绘制一条折线。
    def _draw_line(self, painter: QPainter, points: list[QPointF], color: QColor) -> None:
        painter.setPen(QPen(color, 2))
        for start, end in zip(points, points[1:]):
            painter.drawLine(start, end)
        painter.setBrush(color)
        for point in points:
            painter.drawEllipse(point, 4, 4)


class DashboardPage(QWidget):
    # 图表总览页，展示总金额、开票、收票、收款率和即将到期合同。
    def __init__(self, database: Database, parent=None) -> None:
        super().__init__(parent)
        self.database = database

        self.total_contract_label = QLabel()
        self.total_invoice_label = QLabel()
        self.total_payment_label = QLabel()
        self.payment_rate_label = QLabel()
        self.chart = LineChartWidget()
        self.expiring_table = QTableWidget(0, 4)
        self.expiring_table.setHorizontalHeaderLabels(["合同名称", "合同编号", "甲方名称", "截止日期"])
        self.expiring_table.verticalHeader().setVisible(False)
        self.expiring_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.expiring_table.horizontalHeader().setStretchLastSection(True)

        layout = QVBoxLayout(self)
        title = QLabel("图表总览")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        metric_row = QGridLayout()
        metric_row.addWidget(self._metric_card("总项目金额", self.total_contract_label), 0, 0)
        metric_row.addWidget(self._metric_card("总项目开票", self.total_invoice_label), 0, 1)
        metric_row.addWidget(self._metric_card("总项目收票", self.total_payment_label), 0, 2)
        metric_row.addWidget(self._metric_card("收款率", self.payment_rate_label), 0, 3)
        layout.addLayout(metric_row)
        layout.addWidget(self._card(self.chart))
        layout.addWidget(QLabel("30 天内即将到期项目"))
        layout.addWidget(self.expiring_table)

    # 创建指标卡片。
    def _metric_card(self, title: str, value_label: QLabel) -> QFrame:
        value_label.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #667085;")
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card

    # 给图表加一个简单卡片边框。
    def _card(self, widget: QWidget) -> QFrame:
        card = QFrame()
        card.setObjectName("chartCard")
        layout = QHBoxLayout(card)
        layout.addWidget(widget)
        return card

    # 刷新图表页所有统计数据。
    def refresh(self) -> None:
        total_contract = self.database.total_amount()
        total_invoice = self.database.total_invoice_amount()
        total_payment = self.database.total_payment_amount()
        payment_rate = (total_payment / total_contract * 100) if total_contract else 0

        self.total_contract_label.setText(f"¥ {total_contract:,.2f}")
        self.total_invoice_label.setText(f"¥ {total_invoice:,.2f}")
        self.total_payment_label.setText(f"¥ {total_payment:,.2f}")
        self.payment_rate_label.setText(f"{payment_rate:.1f}%")
        self.chart.set_records(self.database.record_totals_by_date())
        self._fill_expiring_table()

    # 填充即将到期项目表格。
    def _fill_expiring_table(self) -> None:
        contracts = self.database.expiring_contracts(30)
        self.expiring_table.setRowCount(len(contracts))
        for row, contract in enumerate(contracts):
            values = [
                contract.contract_name,
                contract.contract_number,
                contract.party_name,
                contract.end_date,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.expiring_table.setItem(row, column, item)
