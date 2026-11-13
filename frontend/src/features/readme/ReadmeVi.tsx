import Example from './Example'
import { readmeClass } from './readmeClass'
import usageButton from './usage-button.png'

export default function ReadmeVi({ marketStatus, onExample }: { marketStatus: string; onExample(text: string): void }) {
  return (
    <div className={readmeClass}>
      <h1>Hướng dẫn sử dụng smallie</h1>
      <p>Mở từng mục bên dưới để xem chi tiết.</p>

      <details>
        <summary>smallie là gì?</summary>
        <p>smallie là trợ lý trò chuyện với bốn chuyên gia. Bạn chỉ cần hỏi. <strong>Trợ lý</strong> tự trả lời các câu hỏi
          chung, chuyển câu hỏi cho chuyên gia phù hợp, hoặc hỏi nhiều chuyên gia rồi tổng hợp câu trả lời.
          Nhãn phía trên mỗi câu trả lời cho biết ai đã trả lời.</p>
        <ul>
          <li>Bạn có thể viết bằng tiếng Việt hoặc tiếng Anh; câu trả lời sẽ dùng ngôn ngữ bạn đã viết.</li>
          <li>Mỗi người có tài khoản riêng: cuộc trò chuyện, tệp và dữ liệu của bạn chỉ mình bạn thấy được.</li>
          <li>Nói rõ điều bạn cần: tên công ty hoặc mã cổ phiếu, khoảng thời gian, số lượng kết quả.</li>
          <li>Nếu chọn nhầm trợ lý, hãy nói rõ, ví dụ "theo tài liệu của tôi…" hoặc "từ cơ sở dữ liệu cổ phiếu…".</li>
        </ul>
      </details>

      <details>
        <summary>Các trợ lý</summary>
        <details>
          <summary>Tài liệu</summary>
          <p>Trả lời dựa trên các tệp PDF và Word bạn tải lên, kèm trích dẫn tên tệp và số trang.</p>
          <ol>
            <li>Bấm <strong>Tải lên PDF / DOCX</strong> và chọn một hoặc nhiều tệp (tối đa 25 MB mỗi tệp).</li>
            <li>Chờ đến khi tệp hiện <em>sẵn sàng</em>. Việc xử lý mất từ vài giây đến một phút.</li>
            <li>Đặt câu hỏi. Bấm vào một ví dụ để đưa nó vào ô soạn tin:</li>
          </ol>
          <div className="flex flex-wrap gap-2">
            <Example text={"Tóm tắt các ý chính trong tài liệu của tôi."} onExample={onExample} />
            <Example text={"Hợp đồng quy định gì về việc chấm dứt?"} onExample={onExample} />
          </div>
          <p className="text-sm text-muted-foreground">Chỉ bạn mới thấy được tệp của mình. Xoá tệp bằng dấu × bên cạnh tên tệp.</p>
        </details>
        <details>
          <summary>Cơ sở dữ liệu cổ phiếu</summary>
          <p>200 cổ phiếu được nắm giữ nhiều nhất (Apple, Microsoft, NVIDIA, Tesla, JPMorgan, Coca-Cola…), gồm hồ sơ công ty,
            các chỉ số chính và giá theo ngày trong khoảng 5 năm. Trợ lý trả lời bằng cách viết và chạy câu lệnh SQL chỉ đọc;
            mở mục <em>Đã dùng công cụ</em> phía trên câu trả lời để xem câu lệnh đã chạy.</p>
          <p>{marketStatus}</p>
          <div className="flex flex-wrap gap-2">
            <Example text={"10 công ty có vốn hoá lớn nhất là những công ty nào?"} onExample={onExample} />
            <Example text={"NVDA, AMD và INTC biến động thế nào trong 12 tháng qua?"} onExample={onExample} />
            <Example text={"Cổ phiếu nào tăng mạnh nhất trong phiên gần nhất?"} onExample={onExample} />
            <Example text={"So sánh P/E trung bình theo từng ngành."} onExample={onExample} />
            <Example text={"Giá đóng cửa cuối mỗi tháng của Apple trong năm nay."} onExample={onExample} />
          </div>
          <p className="text-sm text-muted-foreground">Giá là dữ liệu cuối ngày từ Yahoo Finance, không phải giá trực tiếp. Dữ liệu tự động cập nhật vào mỗi tối
            ngày làm việc, sau khi thị trường Mỹ đóng cửa. Đây không phải lời khuyên đầu tư.</p>
        </details>
        <details>
          <summary>Nghiên cứu</summary>
          <p>Hãy nói "nghiên cứu", "tìm hiểu", "tin tức mới nhất về…" hoặc yêu cầu một báo cáo. Trợ lý tìm trên web, Wikipedia
            và arXiv (cùng cơ sở dữ liệu cổ phiếu và tệp của bạn khi hữu ích), rồi trả lời bằng bản tóm tắt, hoặc bằng báo cáo,
            bảng so sánh hay hồ sơ công ty kèm nguồn khi bạn yêu cầu.</p>
          <div className="flex flex-wrap gap-2">
            <Example text={"Tìm hiểu tin tức mới nhất về NVIDIA và so sánh với diễn biến giá cổ phiếu."} onExample={onExample} />
            <Example text={"Viết báo cáo ngắn về retrieval-augmented generation, kèm các bài báo gần đây."} onExample={onExample} />
          </div>
          <p className="text-sm text-muted-foreground">Nghiên cứu cần nhiều lượt gọi AI hơn các câu hỏi khác, nên chậm hơn và tốn nhiều hạn mức của bạn hơn.</p>
        </details>
        <details>
          <summary>Biểu đồ</summary>
          <p>Vẽ biểu đồ đường, cột, vùng, tròn và phân tán. Hãy nói "vẽ biểu đồ", "biểu đồ" hoặc "đồ thị". Sau một câu trả lời từ
            cơ sở dữ liệu, "vẽ biểu đồ cho kết quả đó" sẽ vẽ ngay mà không truy vấn lại. Rê chuột lên biểu đồ để xem giá trị;
            bên dưới, <em>Xem dữ liệu</em> liệt kê các số liệu và <em>Tải ảnh PNG</em> lưu hình.</p>
          <div className="flex flex-wrap gap-2">
            <Example text={"Vẽ biểu đồ giá đóng cửa của AAPL và MSFT trong một năm qua."} onExample={onExample} />
            <Example text={"Vẽ biểu đồ vốn hóa của 10 công ty công nghệ lớn nhất."} onExample={onExample} />
          </div>
        </details>
      </details>

      <details>
        <summary>Chế độ trò chuyện</summary>
        <ul>
          <li><strong>1-1</strong> (mặc định): Trợ lý chọn ai sẽ trả lời tin nhắn của bạn.</li>
          <li><strong>Chế độ nhóm</strong> (công tắc ở thanh bên): Trợ lý lập kế hoạch giao nhiệm vụ cho nhiều chuyên gia, cho bạn
            thấy họ làm việc như một nhóm, rồi viết câu trả lời. Chế độ này tốn nhiều lượt gọi AI hơn.</li>
          <li><strong>+ Cuộc trò chuyện mới</strong> bắt đầu một cuộc trò chuyện mới. Các cuộc trước nằm ở thanh bên. Có thể hỏi tiếp: "còn 3 năm thì sao?", "hiển thị dạng bảng".</li>
          <li>Nút <em>Hoạt động</em> phía trên câu trả lời cho biết điều đã diễn ra phía sau câu trả lời đó.</li>
        </ul>
      </details>

      <details>
        <summary>Theo dõi mức sử dụng</summary>
        <p>Nút <strong>Mức sử dụng</strong> bên dưới ô trò chuyện mở ra số token và chi phí của cuộc trò chuyện này, cho câu trả
          lời gần nhất và cho tổng cộng. Hộp ở thanh bên cho biết tổng của tài khoản bạn.</p>
        <img src={usageButton} alt="Nút Mức sử dụng nằm bên dưới nút Gửi của ô trò chuyện" className="rounded-md border" />
      </details>
    </div>
  )
}
