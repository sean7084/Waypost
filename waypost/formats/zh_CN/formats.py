# Simplified-Chinese display formats so the locale-aware |date:"DATE_FORMAT"
# family renders Chinese dates (Django's built-in zh_CN formats omit several of
# these, which previously fell back to English month names).

DATE_FORMAT = 'Y年n月j日'
DATETIME_FORMAT = 'Y年n月j日 H:i'
SHORT_DATE_FORMAT = 'Y-n-j'
SHORT_DATETIME_FORMAT = 'Y-n-j H:i'
TIME_FORMAT = 'H:i'
YEAR_MONTH_FORMAT = 'Y年n月'
MONTH_DAY_FORMAT = 'n月j日'
DAY_MONTH_FORMAT = 'n月j日'

DATE_INPUT_FORMATS = ['%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y', '%m/%d/%Y']
DATETIME_INPUT_FORMATS = ['%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']

FIRST_DAY_OF_WEEK = 1
DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','
NUMBER_GROUPING = 3
