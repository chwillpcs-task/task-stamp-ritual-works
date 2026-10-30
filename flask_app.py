import os
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder='templates')

submissions = []

@app.route('/')
def index():
    return "TASK / Our Times / taskl3c Web App"

@app.route('/plot', methods=['GET', 'POST'])
def plot():
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form.to_dict()
        
        place = data.get('place', '')
        era = data.get('era', '')
        story = data.get('story', '')
        
        record = {
            'place': place,
            'era': era,
            'story': story
        }
        submissions.append(record)
        
        return jsonify({
            'status': 'success',
            'message': 'Submission recorded successfully',
            'data': record
        }), 200

    return render_template('plot.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
